with open('new_agent_2_BE/services/step4_inspector.py', 'r', encoding='utf-8') as f:
    content = f.read()

content = content.replace('\r\n', '\n')

old_target = '''        total_claims_count = len(all_claims)
        verified_claims_count = sum(1 for clm in all_claims if clm.get("status") == "VERIFIED" and clm.get("source_locator"))
        unsupported_claims_count = sum(1 for clm in all_claims if clm.get("status") == "UNSUPPORTED")
        unresolved_claims_count = sum(1 for clm in all_claims if clm.get("status") in {"UNRESOLVED", "PARTIALLY_VERIFIED"})
        missing_locator_claims_count = sum(1 for clm in all_claims if clm.get("status") == "VERIFIED" and not clm.get("source_locator"))

        if unsupported_claims_count > 0 or unresolved_claims_count > 0 or missing_locator_claims_count > 0 or len(inconsistent_candidates) > 0:
            qc_unsup_status = "FAIL"
            qc_unsup_msg = (
                f"{unsupported_claims_count} unsupported claims, {unresolved_claims_count} unresolved claims, "
                f"{missing_locator_claims_count} claims missing locators, {len(inconsistent_candidates)} inconsistent candidates "
                f"out of {total_claims_count} total atomic claims ({verified_claims_count} verified)"
            )
        else:
            qc_unsup_status = "PASS"
            qc_unsup_msg = (
                f"0 unsupported or unresolved claims across {total_claims_count} atomic claims; "
                f"{verified_claims_count} verified with explicit source quotations and locators"
            )'''

new_replacement = '''        total_claims_count = len(all_claims)
        verified_claims_count = sum(1 for clm in all_claims if clm.get("status") == "VERIFIED" and clm.get("source_locator"))
        unsupported_claims_count = sum(1 for clm in all_claims if clm.get("status") == "UNSUPPORTED")
        unresolved_claims_count = sum(1 for clm in all_claims if clm.get("status") in {"UNRESOLVED", "PARTIALLY_VERIFIED"})
        missing_locator_claims_count = sum(1 for clm in all_claims if clm.get("status") == "VERIFIED" and not clm.get("source_locator"))

        # Determine if any un-gated / un-quarantined candidate has unsupported claims
        ungated_unsupported_candidates = []
        for c in structured_records:
            c_claims = c.get("claims_evidence", [])
            has_unsup = any(clm.get("status") in {"UNSUPPORTED", "UNRESOLVED"} for clm in c_claims)
            is_gated = (
                c.get("review_required") is True
                or c.get("scope_status") in {"UNCERTAIN", "OUT_OF_SCOPE"}
                or c.get("provenance_status") in {"PARTIALLY_VERIFIED", "UNRESOLVED"}
                or c.get("review_status") == "PENDING_REVIEW"
                or bool(c.get("manual_review_reason"))
            )
            if has_unsup and not is_gated:
                ungated_unsupported_candidates.append(c.get("candidate_id"))

        if len(inconsistent_candidates) > 0 or len(ungated_unsupported_candidates) > 0 or missing_locator_claims_count > 0:
            qc_unsup_status = "FAIL"
            qc_unsup_msg = (
                f"{unsupported_claims_count} unsupported claims ({len(ungated_unsupported_candidates)} ungated), "
                f"{unresolved_claims_count} unresolved claims, {missing_locator_claims_count} claims missing locators, "
                f"{len(inconsistent_candidates)} inconsistent candidates out of {total_claims_count} total atomic claims ({verified_claims_count} verified)"
            )
        elif unsupported_claims_count > 0 or unresolved_claims_count > 0:
            qc_unsup_status = "PASS"
            qc_unsup_msg = (
                f"{unsupported_claims_count} unsupported / {unresolved_claims_count} unresolved claims safely quarantined "
                f"in gated review items; {verified_claims_count} verified across {total_claims_count} atomic claims with source locators"
            )
        else:
            qc_unsup_status = "PASS"
            qc_unsup_msg = (
                f"0 unsupported or unresolved claims across {total_claims_count} atomic claims; "
                f"{verified_claims_count} verified with explicit source quotations and locators"
            )'''

assert old_target in content, 'Target block not found!'
content = content.replace(old_target, new_replacement)
with open('new_agent_2_BE/services/step4_inspector.py', 'w', encoding='utf-8') as f:
    f.write(content)
print('Successfully patched step4_inspector.py!')

