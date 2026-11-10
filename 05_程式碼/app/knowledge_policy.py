"""Shared, deterministic knowledge eligibility. No network or model calls.

Review approval binds to the exact body; a new crawl date is not an approval.
Known obsolete claims cannot be approved back into current-policy answers.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import re


def body_hash(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def policy_claim(text: str) -> tuple[str | None, bool]:
    t = re.sub(r'\s+', ' ', text).lower()
    families = []
    arc = bool(re.search(r'\barc\b|alien resident certificate|外僑居留證|居留證', t))
    arrival = r'(?:arrival|arriv(?:al|ing|e)|receiving (?:a )?resident visa|入[境國])'
    if arc:
        for days, old in [(r'(?:15|十五)', True), (r'(?:30|三十)', False)]:
            duration = r'(?<![0-9])' + days + r'\s*(?:days?\b|日|天)'
            if re.search(arrival + r'.{0,130}?' + duration, t) or re.search(duration + r'.{0,100}?' + arrival, t):
                if old:
                    return 'arc_arrival_deadline', True
                families.append('arc_arrival_deadline')
    if re.search(r'work permit|工作許可|工作證', t):
        if re.search(r'(?:最長|許可期間|有效期間|max(?:imum)?|valid|duration).{0,65}?(?<![0-9])(?:6\s*months?\b|6\s*個月|六個月)', t):
            return 'student_work_permit_duration', True
        if re.search(r'(?:最長|許可期間|有效期間|max(?:imum)?|valid|duration).{0,65}?(?<![0-9])(?:1\s*year\b|one year\b|一年)', t):
            families.append('student_work_permit_duration')
    return '+'.join(families) or None, False


def date_value(value) -> dt.date | None:
    if not value:
        return None
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    s = str(value)
    try:
        return dt.datetime.strptime(s, '%Y%m%d').date() if re.fullmatch(r'\d{8}', s) else dt.date.fromisoformat(s[:10])
    except ValueError:
        return None


def review_reason(text: str, metadata: dict, today: dt.date, stale_before: dt.date) -> str | None:
    family, obsolete = policy_claim(text)
    if obsolete:
        return 'known_obsolete_policy:' + family
    if not family:
        return None
    reviewed = date_value(metadata.get('policy_reviewed_at'))
    if (metadata.get('policy_review_status') != 'approved'
            or metadata.get('policy_reviewed_sha256') != body_hash(text)
            or reviewed is None or not stale_before <= reviewed <= today):
        return 'policy_needs_human_review:' + family
    return None


def neighboring_policy_ids(cid: str, md: dict) -> list[str]:
    if md.get('source') == 'manual' or cid.startswith(('md','faq-','sheet-')):
        return []
    doc, _, part = cid.rpartition('-')
    if not doc or not part.isdigit():
        return []
    n = int(part)
    return ([f'{doc}-{n-1}'] if n else []) + [f'{doc}-{n+1}']


def eligible(text: str, md: dict, *, identity: str | None, topic: str | None,
             today: dt.date, stale_before: dt.date) -> bool:
    context_rule = md.get('policy_context_rule') or ''
    if context_rule.startswith('obsolete:'):
        return False
    # Reviewable regulations must stand on their own in the approved body.
    # A fragment inheriting a claim needs curation/rechunking before approval.
    if context_rule.startswith('review:') and not policy_claim(text)[0]:
        return False
    verified = date_value(md.get('last_verified'))
    expires = date_value(md.get('valid_until'))
    if md.get('status') != 'active' or verified is None or verified < stale_before:
        return False
    # Invalid nonempty deadlines fail closed, rather than becoming no deadline.
    if md.get('valid_until') and expires is None:
        return False
    if expires is not None and expires < today:
        return False
    identities = md.get('identities') or []
    if identity and identity not in identities and '全體境外生' not in identities and not md.get('id_' + identity) and not md.get('id_全體境外生'):
        return False
    if topic and topic not in (md.get('topics') or []) and not md.get('t_' + topic):
        return False
    return review_reason(text, md, today, stale_before) is None
