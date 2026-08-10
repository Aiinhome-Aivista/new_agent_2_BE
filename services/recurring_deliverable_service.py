"""
RecurringDeliverableService
"""

from __future__ import annotations

import json
from calendar import monthrange
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

from services.llm_service import LLMService
from core.prompts import get_recurrence_extraction_prompt

RECURRENCE_CONFIDENCE_THRESHOLD = 0.75
PARTIAL_PERIOD_MIN_DAYS = 15
BATCH_SIZE = 8


class RecurringDeliverableService:

    @classmethod
    def process_recurring_commitments(cls, db, baseline_id, project_id, scope_items, project):
        project_start = cls._parse_date(project.get("start_date"))
        project_end = cls._parse_date(project.get("end_date"))
        if not project_start or not project_end:
            print("[Recurring] Project has no start/end date.")
            return

        candidates = [i for i in scope_items if i.get("scope_type") == "IN_SCOPE" and i.get("_db_id")]
        if not candidates:
            print("[Recurring] No IN_SCOPE candidates.")
            return

        print(f"[Recurring] Analysing {len(candidates)} IN_SCOPE candidates...")
        recurrence_results = cls._extract_recurrence_batch(candidates)

        recurring_count = 0
        occurrence_count = 0
        for item, result in zip(candidates, recurrence_results):
            if not result.get("is_recurring"):
                continue
            frequency = result.get("frequency", "").upper()
            if frequency not in ("WEEKLY", "MONTHLY", "QUARTERLY", "YEARLY"):
                continue
            confidence = float(result.get("confidence", 0.0))
            parent_id = item["_db_id"]
            cls._update_parent_recurrence_fields(db, parent_id, frequency, confidence, result.get("start_date"), result.get("end_date"))
            if confidence < RECURRENCE_CONFIDENCE_THRESHOLD:
                print(f"[Recurring] '{item.get('name')}' confidence {confidence:.2f} < threshold — tagged only.")
                continue
            eff_start = cls._parse_date(result.get("start_date")) or project_start
            eff_end = cls._parse_date(result.get("end_date")) or project_end
            eff_start = max(eff_start, project_start)
            eff_end = min(eff_end, project_end)
            occurrences = cls._generate_occurrences(frequency, eff_start, eff_end, item)
            for occ in occurrences:
                cls._upsert_occurrence(db, baseline_id, project_id, parent_id, item, occ, item.get("source_document_id"))
                occurrence_count += 1
            recurring_count += 1
            print(f"[Recurring] '{item.get('name')}' -> {frequency}, {len(occurrences)} occurrences")

        db.commit()
        print(f"[Recurring] Done — {recurring_count} recurring, {occurrence_count} occurrences.")

    @classmethod
    def _extract_recurrence_batch(cls, candidates):
        results = [{"is_recurring": False}] * len(candidates)
        for batch_start in range(0, len(candidates), BATCH_SIZE):
            batch = candidates[batch_start:batch_start + BATCH_SIZE]
            items_for_prompt = [{"id": str(i), "name": c.get("name",""), "description": c.get("description",""), "evidence_text": c.get("evidence_text","")} for i, c in enumerate(batch)]
            prompt = get_recurrence_extraction_prompt(items_for_prompt)
            try:
                batch_results = LLMService.generate_json(prompt)
                if not isinstance(batch_results, list):
                    batch_results = [batch_results]
                result_map = {str(r.get("id","")): r for r in batch_results}
                for i in range(len(batch)):
                    results[batch_start + i] = result_map.get(str(i), {"is_recurring": False})
            except Exception as exc:
                print(f"[Recurring] LLM batch failed: {exc}")
        return results

    @classmethod
    def _generate_occurrences(cls, frequency, eff_start, eff_end, parent_item):
        if frequency == "MONTHLY":
            return cls._monthly_occurrences(eff_start, eff_end, parent_item)
        if frequency == "QUARTERLY":
            return cls._quarterly_occurrences(eff_start, eff_end, parent_item)
        if frequency == "WEEKLY":
            return cls._weekly_occurrences(eff_start, eff_end, parent_item)
        if frequency == "YEARLY":
            return cls._yearly_occurrences(eff_start, eff_end, parent_item)
        return []

    @classmethod
    def _monthly_occurrences(cls, eff_start, eff_end, parent):
        results = []
        year, month = eff_start.year, eff_start.month
        while True:
            period_end = date(year, month, monthrange(year, month)[1])
            if period_end > eff_end:
                break
            period_start_for_this = date(year, month, 1)
            effective_from = max(period_start_for_this, eff_start)
            days_in_period = (period_end - effective_from).days + 1
            if days_in_period >= PARTIAL_PERIOD_MIN_DAYS or period_start_for_this >= eff_start:
                period_key = f"{year}-{month:02d}"
                label = period_end.strftime("%b %Y")
                results.append(cls._occ(parent, period_key, label, period_end))
            month += 1
            if month > 12:
                month = 1
                year += 1
        return results

    @classmethod
    def _quarterly_occurrences(cls, eff_start, eff_end, parent):
        QUARTER_END = {1: (3,31), 2: (6,30), 3: (9,30), 4: (12,31)}
        results = []
        year = eff_start.year
        q = (eff_start.month - 1) // 3 + 1
        while True:
            end_month, end_day = QUARTER_END[q]
            period_end = date(year, end_month, end_day)
            if period_end > eff_end:
                break
            if period_end >= eff_start:
                results.append(cls._occ(parent, f"{year}-Q{q}", f"Q{q} {year}", period_end))
            q += 1
            if q > 4:
                q = 1
                year += 1
        return results

    @classmethod
    def _weekly_occurrences(cls, eff_start, eff_end, parent):
        results = []
        days_ahead = 6 - eff_start.weekday()
        if days_ahead < 0:
            days_ahead += 7
        current_sunday = eff_start + timedelta(days=days_ahead)
        while current_sunday <= eff_end:
            iso = current_sunday.isocalendar()
            period_key = f"{iso[0]}-W{iso[1]:02d}"
            label = f"Wk {iso[1]} {iso[0]}"
            results.append(cls._occ(parent, period_key, label, current_sunday))
            current_sunday += timedelta(weeks=1)
        return results

    @classmethod
    def _yearly_occurrences(cls, eff_start, eff_end, parent):
        results = []
        year = eff_start.year
        while True:
            period_end = date(year, 12, 31)
            if period_end > eff_end:
                if date(year, 1, 1) <= eff_end:
                    results.append(cls._occ(parent, str(year), str(year), eff_end))
                break
            if period_end >= eff_start:
                results.append(cls._occ(parent, str(year), str(year), period_end))
            year += 1
        return results

    @staticmethod
    def _occ(parent, period_key, period_label, due_date):
        return {"period_key": period_key, "period_label": period_label, "due_date": due_date, "title": f"{parent.get('name','Recurring Commitment')} — {period_label}"}

    @classmethod
    def _update_parent_recurrence_fields(cls, db, item_id, frequency, confidence, recurrence_start, recurrence_end):
        cursor = db.cursor()
        cursor.execute(
            "UPDATE scope_items SET is_recurring=1, recurrence_frequency=%s, recurrence_confidence=%s, recurrence_start_date=%s, recurrence_end_date=%s, recurrence_source='EL' WHERE id=%s",
            (frequency, confidence, recurrence_start or None, recurrence_end or None, item_id)
        )
        cursor.close()

    @classmethod
    def _upsert_occurrence(cls, db, baseline_id, project_id, parent_id, parent_item, occurrence, source_document_id):
        cursor = db.cursor()
        due_str = occurrence["due_date"].isoformat()
        cursor.execute(
            """INSERT INTO scope_items
               (baseline_id,project_id,name,scope_item_normalized,description,scope_type,
                source_document_id,evidence_text,confidence,deadline,deadline_normalized,
                deadline_original,deadline_text,is_recurring,recurrence_frequency,
                parent_scope_item_id,occurrence_period,recurrence_source,category,completion_status)
               VALUES (%s,%s,%s,%s,%s,'IN_SCOPE',%s,%s,%s,%s,%s,%s,%s,1,%s,%s,%s,'EL',%s,'ACTIVE')
               ON DUPLICATE KEY UPDATE
                   deadline=VALUES(deadline),deadline_normalized=VALUES(deadline_normalized),
                   deadline_original=VALUES(deadline_original),deadline_text=VALUES(deadline_text),
                   name=VALUES(name)""",
            (baseline_id, project_id, occurrence["title"], occurrence["title"].lower(),
             f"Recurring occurrence from: {parent_item.get('name','')}",
             source_document_id,
             parent_item.get("evidence_text","Generated from recurring EL commitment"),
             parent_item.get("confidence",1.0),
             due_str, due_str, occurrence["period_label"], occurrence["period_label"],
             parent_item.get("recurrence_frequency","MONTHLY"),
             parent_id, occurrence["period_key"],
             parent_item.get("category","DELIVERABLE"))
        )
        cursor.close()

    @staticmethod
    def _parse_date(value):
        if not value:
            return None
        if isinstance(value, date):
            return value
        try:
            clean = str(value).split("T")[0].split(" ")[0]
            parts = clean.split("-")
            return date(int(parts[0]), int(parts[1]), int(parts[2]))
        except Exception:
            return None

    @classmethod
    def extend_occurrences_for_project(cls, db, baseline_id, project_id, parent_id, new_project_end, project_start):
        cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT * FROM scope_items WHERE id=%s", (parent_id,))
        parent = cursor.fetchone()
        cursor.close()
        if not parent or not parent.get("is_recurring"):
            return 0
        frequency = parent.get("recurrence_frequency")
        if not frequency:
            return 0
        cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT MAX(deadline) as last_due FROM scope_items WHERE parent_scope_item_id=%s", (parent_id,))
        row = cursor.fetchone()
        cursor.close()
        last_due = cls._parse_date(row["last_due"]) if row and row.get("last_due") else project_start
        new_start = last_due + timedelta(days=1)
        if new_start > new_project_end:
            return 0
        occurrences = cls._generate_occurrences(frequency, new_start, new_project_end, dict(parent))
        count = 0
        for occ in occurrences:
            cls._upsert_occurrence(db, baseline_id, project_id, parent_id, dict(parent), occ, parent.get("source_document_id"))
            count += 1
        if count:
            db.commit()
        return count

    @classmethod
    def trim_occurrences_after_date(cls, db, parent_id, new_end_date):
        cursor = db.cursor()
        cursor.execute(
            """UPDATE scope_items si LEFT JOIN deliverable_progress dp ON dp.scope_item_id=si.id
               SET si.completion_status='CANCELLED'
               WHERE si.parent_scope_item_id=%s AND si.deadline>%s AND dp.id IS NULL""",
            (parent_id, new_end_date.isoformat())
        )
        affected = cursor.rowcount
        cursor.close()
        db.commit()
        return affected
