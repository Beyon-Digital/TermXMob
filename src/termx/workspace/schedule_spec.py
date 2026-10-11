"""Bounded wall-clock scheduling; five-field numeric cron, no shell evaluation."""
from datetime import datetime, timedelta
from math import isfinite
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def cron_field(source, low, high):
    values = set()
    for part in source.split(','):
        base, slash, step = part.partition('/')
        if slash and (not step.isdigit() or not 1 <= int(step) <= high-low+1):
            raise ValueError('Invalid cron step')
        stride = int(step) if slash else 1
        if base == '*':
            first, last = low, high
        elif '-' in base:
            bounds = base.split('-')
            if len(bounds) != 2 or not all(v.isdigit() for v in bounds):
                raise ValueError('Invalid cron range')
            first, last = map(int, bounds)
        elif base.isdigit():
            first = int(base); last = high if slash else first
        else:
            raise ValueError('Cron uses numbers, *, ranges, lists and steps')
        if not low <= first <= last <= high:
            raise ValueError('Cron field is outside its allowed range')
        values.update(range(first, last+1, stride))
    return values


def next_run(spec: dict, after: float) -> float:
    if not isinstance(spec, dict):
        raise ValueError('Schedule must be an object')
    kind = spec.get('kind')
    if kind == 'once':
        stamp = spec.get('at')
        if isinstance(stamp, bool) or not isinstance(stamp, (int, float)) or not isfinite(stamp) or stamp <= after:
            raise ValueError('Choose a future date and time')
        return stamp
    if kind == 'interval':
        seconds = spec.get('seconds')
        if isinstance(seconds, bool) or not isinstance(seconds, int) or not 60 <= seconds <= 31_536_000:
            raise ValueError('Interval must be 60 seconds to one year')
        return after + seconds
    try:
        zone = ZoneInfo(spec['timezone'])
    except (KeyError, TypeError, ValueError, ZoneInfoNotFoundError):
        raise ValueError('Choose a valid IANA timezone, for example UTC') from None
    if kind == 'daily':
        try:
            hour, minute = map(int, spec['time'].split(':'))
            if not (0 <= hour <= 23 and 0 <= minute <= 59):
                raise ValueError
        except (KeyError, AttributeError, TypeError, ValueError):
            raise ValueError('Daily schedule requires HH:MM') from None
        hours, minutes = {hour}, {minute}
        months, days, weekdays = set(range(1,13)), set(range(1,32)), set(range(7))
        day_any = weekday_any = True
    elif kind == 'cron':
        expression = spec.get('expression')
        if not isinstance(expression, str) or len(expression) > 200 or len(expression.split()) != 5:
            raise ValueError('Cron requires five fields: minute hour day month weekday')
        parts = expression.split()
        minutes, hours, days, months, weekdays = [cron_field(part, low, high) for part, (low, high) in zip(parts, [(0,59),(0,23),(1,31),(1,12),(0,7)])]
        weekdays = {v % 7 for v in weekdays}
        day_any, weekday_any = parts[2].startswith('*'), parts[4].startswith('*')
    else:
        raise ValueError('Supported schedules: once, daily, interval and cron')
    local = datetime.fromtimestamp(after, zone)
    # Eight years includes leap-day occurrences across non-leap centuries.
    for offset in range(366 * 8):
        day = local.date() + timedelta(days=offset)
        dom, dow = day.day in days, (day.weekday()+1) % 7 in weekdays
        matches_day = dom and dow if day_any or weekday_any else dom or dow
        if day.month not in months or not matches_day:
            continue
        for hour in sorted(hours):
            for minute in sorted(minutes):
                candidate = datetime(day.year, day.month, day.day, hour, minute, tzinfo=zone, fold=0)
                stamp = candidate.timestamp()
                back = datetime.fromtimestamp(stamp, zone)
                # Nonexistent times are skipped; repeated wall-clock times run once.
                if stamp > after and back.replace(tzinfo=None) == candidate.replace(tzinfo=None):
                    return stamp
    raise ValueError('Cron has no occurrence in the next eight years')
