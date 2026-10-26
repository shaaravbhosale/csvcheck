import csv
import json
import math
import re
import shutil
import time
from datetime import date
from pathlib import Path

from .models import Rules

MAX_ROWS = 100_000
EMAIL = re.compile(r'[^\s@]+@[^\s@]+\.[^\s@]+')


def validate(value, rule):
    if not value:
        return 'required value is missing' if rule.required else None
    if rule.kind == 'email' and not EMAIL.fullmatch(value):
        return 'expected an email address'
    if rule.kind == 'date':
        try:
            if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
                raise ValueError
            date.fromisoformat(value)
        except ValueError:
            return 'expected a real date in YYYY-MM-DD format'
    if rule.kind in ('integer', 'number'):
        try:
            if rule.kind == 'integer' and not re.fullmatch(r'[+-]?\d+', value):
                return 'expected an integer'
            number = float(value)
            if not math.isfinite(number):
                return 'expected a finite number'
        except (ValueError, OverflowError):
            return 'expected a number'
        if rule.minimum is not None and number < rule.minimum:
            return f'must be at least {rule.minimum:g}'
        if rule.maximum is not None and number > rule.maximum:
            return f'must be at most {rule.maximum:g}'
    return None


def process(input_path: Path, job_dir: Path, rules: Rules):
    """Stream rows to a staging directory; publish results only after a complete pass."""
    started = time.perf_counter()
    staging = job_dir / 'staging'
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir()
    counts = {'total': 0, 'kept': 0, 'invalid': 0, 'duplicates': 0, 'normalized_rows': 0}
    preview, seen, error_counts = [], set(), {}

    with input_path.open(encoding='utf-8-sig', newline='') as source:
        reader = csv.reader(source, strict=True)
        try:
            headers = next(reader)
        except StopIteration:
            raise ValueError('The CSV is empty.')
        headers = [name.strip() for name in headers]
        if not headers or any(not h for h in headers) or len(headers) != len(set(headers)):
            raise ValueError('CSV headers must be nonempty and unique after trimming.')
        if len(headers) > 100:
            raise ValueError('CSV files may have at most 100 columns.')
        needed = {rule.name for rule in rules.columns} | set(rules.deduplicate_by)
        missing = needed - set(headers)
        if missing:
            raise ValueError('Missing columns: ' + ', '.join(sorted(missing)))
        indexes = {name: i for i, name in enumerate(headers)}

        with (staging / 'cleaned.csv').open('w', encoding='utf-8', newline='') as clean_file, \
             (staging / 'rejected.csv').open('w', encoding='utf-8', newline='') as reject_file, \
             (staging / 'errors.csv').open('w', encoding='utf-8', newline='') as error_file:
            clean, rejected, errors = csv.writer(clean_file), csv.writer(reject_file), csv.writer(error_file)
            clean.writerow(headers)
            rejected.writerow(['source_record', 'reason', 'original_values_json'])
            errors.writerow(['source_record', 'column', 'message'])
            for record, values in enumerate(reader, start=2):
                counts['total'] += 1
                if counts['total'] > MAX_ROWS:
                    raise ValueError(f'CSV exceeds the {MAX_ROWS:,}-row limit.')
                issues = []
                original = values[:]
                if len(values) != len(headers):
                    issues.append(('*', f'expected {len(headers)} fields, found {len(values)}'))
                else:
                    if rules.trim_whitespace:
                        values = [v.strip() for v in values]
                    for rule in rules.columns:
                        i = indexes[rule.name]
                        if rule.lowercase:
                            values[i] = values[i].lower()
                        message = validate(values[i], rule)
                        if message:
                            issues.append((rule.name, message))
                if values != original:
                    counts['normalized_rows'] += 1
                if issues:
                    counts['invalid'] += 1
                    rejected.writerow([record, 'invalid', json.dumps(original, ensure_ascii=False)])
                    for column, message in issues:
                        errors.writerow([record, column, message])
                        error_counts[column] = error_counts.get(column, 0) + 1
                    continue

                # Invalid rows never reserve a deduplication key. First valid row wins.
                if rules.deduplicate_by:
                    key = tuple(values[indexes[name]] for name in rules.deduplicate_by)
                    if key in seen:
                        counts['duplicates'] += 1
                        rejected.writerow([record, 'duplicate', json.dumps(original, ensure_ascii=False)])
                        continue
                    seen.add(key)
                clean.writerow(values)
                counts['kept'] += 1
                if len(preview) < 10:
                    preview.append(dict(zip(headers, values)))

    summary = {**counts, 'headers': headers, 'preview': preview, 'errors_by_column': error_counts,
               'duration_seconds': round(time.perf_counter() - started, 4)}
    (staging / 'summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
    results = job_dir / 'results'
    if results.exists():
        shutil.rmtree(results)
    staging.rename(results)
    return summary
