from datetime import date, timedelta

def add_working_days(start_d, num_days):
    cur = start_d
    added = 0
    while added < num_days:
        cur += timedelta(days=1)
        if cur.weekday() < 5:
            added += 1
    return cur

base_start = date(2027, 1, 22)
contract_end = date(2027, 4, 30)

# Simulate 81 work items (approx 84 dev-weeks) across 5 developers at 70% utilization
# Each dev delivers 0.7 dev-weeks per 5-day week, so 1 dev-week of effort takes 5 / 0.7 = 7.14 working days (~7 days).
num_devs = 5
dev_available = [base_start] * num_devs

# Effort breakdown: 14 simple (0.5), 57 medium (1.0), 10 complex (2.0)
items = []
for i in range(14):
    items.append({"id": f"simple_{i}", "effort": 0.5, "working_days": 4, "type": "SIMPLE"})
for i in range(57):
    items.append({"id": f"med_{i}", "effort": 1.0, "working_days": 7, "type": "MEDIUM"})
for i in range(10):
    items.append({"id": f"comp_{i}", "effort": 2.0, "working_days": 14, "type": "COMPLEX"})

scheduled = []
for it in items:
    # Pick earliest available dev
    earliest_idx = min(range(num_devs), key=lambda idx: dev_available[idx])
    start_d = dev_available[earliest_idx]
    finish_d = add_working_days(start_d, it["working_days"])
    dev_available[earliest_idx] = finish_d
    scheduled.append({
        "id": it["id"],
        "dev": earliest_idx,
        "start": start_d.isoformat(),
        "finish": finish_d.isoformat(),
        "overrun": finish_d > contract_end
    })

latest_finish = max(s["finish"] for s in scheduled)
overruns = sum(1 for s in scheduled if s["overrun"])
print("Latest finish with 5 parallel developers:", latest_finish)
print(f"Total overrunning items: {overruns} out of {len(scheduled)}")

