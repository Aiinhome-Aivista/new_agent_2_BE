import re

def parse_items(path):
    with open(path, 'r', encoding='utf-8') as f:
        text = f.read()
    
    # Split by object entries: { "id": ... }
    blocks = re.split(r'\{\s*"id":\s*\d+', text)[1:]
    items = []
    for b in blocks:
        def get_field(pat, default=None):
            m = re.search(pat, b)
            return m.group(1) if m else default

        title = get_field(r'"title":\s*"([^"]+)"')
        role = get_field(r'"graph_role":\s*"([^"]+)"')
        status = get_field(r'"status":\s*"([^"]+)"')
        exec_status = get_field(r'"execution_status":\s*"([^"]+)"')
        risk_score = get_field(r'"risk_score":\s*(\d+)')
        exec_prio = get_field(r'"execution_priority_score":\s*(\d+)')
        risk_sev = get_field(r'"risk_severity_score":\s*(\d+)')
        owner = get_field(r'"owner":\s*"([^"]+)"')
        cid = get_field(r'"canonical_id":\s*"([^"]+)"')
        items.append({
            'title': title,
            'role': role,
            'status': status,
            'exec_status': exec_status,
            'risk_score': risk_score,
            'exec_prio': exec_prio,
            'risk_sev': risk_sev,
            'owner': owner,
            'canonical_id': cid,
        })
    return items

d1 = parse_items(r'c:\Users\ADMIN\Desktop\Agent-2\1-9-26-api-response-week14.md')
d2 = parse_items(r'c:\Users\ADMIN\Desktop\Agent-2\25-9-26-api-response-week14.md')

print(f'Count 1-9-26: {len(d1)}')
print(f'Count 25-9-26: {len(d2)}')

print('\n=== ITEMS IN 1-9-26 ===')
for it in d1:
    print(f"Title: {str(it['title'])[:45]:<45} | Role: {str(it['role']):<20} | Stat: {str(it['status']):<8} | ExecStat: {str(it['exec_status']):<10} | Risk: {str(it['risk_score']):<4} | ExecPrio: {str(it['exec_prio']):<4} | RiskSev: {str(it['risk_sev']):<4}")

with open(r'c:\Users\ADMIN\Desktop\Agent-2\1-9-26-api-response-week14.md', 'r', encoding='utf-8') as f:
    text1 = f.read()
with open(r'c:\Users\ADMIN\Desktop\Agent-2\25-9-26-api-response-week14.md', 'r', encoding='utf-8') as f:
    text2 = f.read()

print('\n=== CHAINS IN 25-9-26 ===')
for m in re.finditer(r'"title":\s*"([^"]+)"[\s\S]*?"graph_role":\s*"([^"]+)"[\s\S]*?"canonical_id":\s*"([^"]*)"[\s\S]*?\\"execution_chain\\":\s*(\[[^\]]+\])', text2):
    print(f"Title: {m.group(1):<40} | Role: {m.group(2):<15} | ID: {m.group(3):<10} | Chain: {m.group(4)}")

print('\n=== CHAINS IN 1-9-26 ===')
for m in re.finditer(r'"title":\s*"([^"]+)"[\s\S]*?"graph_role":\s*"([^"]+)"[\s\S]*?"canonical_id":\s*"([^"]*)"[\s\S]*?"execution_chain":\s*(\[[^\]]+\])', text1):
    print(f"Title: {m.group(1):<40} | Role: {m.group(2):<15} | ID: {m.group(3):<10} | Chain: {m.group(4)}")


