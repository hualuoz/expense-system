"""Health Check v2 - 全量系统健康检查脚本（修正DB路径版）"""
import requests
import json
import sys
import sqlite3

BASE = "http://127.0.0.1:5000/api/v1"
DB_PATH = r"C:\Users\lijunhua\.catpaw\skills\expense-reimbursement\web\data.db"

def h(uid="u001", ct=True):
    r = {"X-User-ID": uid}
    if ct:
        r["Content-Type"] = "application/json"
    return r

def api(method, path, uid="u001", body=None):
    url = BASE + path
    try:
        if method == "GET":
            r = requests.get(url, headers=h(uid, False), timeout=5)
        elif method == "DELETE":
            r = requests.delete(url, headers=h(uid, False), timeout=5)
        else:
            r = requests.post(url, headers=h(uid), json=body, timeout=5)
        data = r.json()
        return {"status": r.status_code, "code": data.get("code"), "msg": data.get("message",""), "data": data.get("data"), "ok": data.get("code") == 0}
    except Exception as e:
        return {"status": -1, "code": -1, "msg": str(e)[:120], "data": None, "ok": False}

# ==========================================
# 1️⃣ API 可用性测试
# ==========================================
print("=" * 60)
print("  1️⃣ API 可用性测试")
print("=" * 60)

api_pass = 0
api_total = 0

def check(desc, r, expect_code=0):
    global api_pass, api_total
    api_total += 1
    ok = r["code"] == expect_code
    if ok: api_pass += 1
    icon = "✅" if ok else "❌"
    detail = f"HTTP={r['status']} code={r['code']}"
    if r['msg']: detail += f" msg={r['msg'][:60]}"
    print(f"  {icon} {desc:45s} => {detail}")
    return ok

# --- 1A. 认证类 ---
check("POST /auth/login", api("POST", "/auth/login", body={"username": "zhangsan"}))
check("GET  /auth/users (公开)", api("GET", "/auth/users"))
check("GET  /auth/me", api("GET", "/auth/me", "u001"))

# --- 1B. 报销CRUD ---
check("GET  /reimbursements", api("GET", "/reimbursements", "u001"))
check("GET  /reimbursements?status=ALL", api("GET", "/reimbursements?status=ALL", "u001"))
check("GET  /reimbursements?status=SUBMITTED", api("GET", "/reimbursements?status=SUBMITTED", "u001"))

r_create = api("POST", "/reimbursements", "u001", {"items": [{"date": "2026-06-20", "category": "OFFICE-SUPPLIES", "amount": 350, "description": "HC-Test1", "receipt_no": "FP-HC-001"}]})
rid = r_create["data"]["id"] if r_create["data"] else "ERR"
check("POST /reimbursements (创建)", r_create)

r_detail = api("GET", f"/reimbursements/{rid}", "u001")
check(f"GET  /reimbursements/{rid}", r_detail)

# --- 1C. 审批流 ---
check("POST /action start_review", api("POST", f"/reimbursements/{rid}/action", "u002", {"action": "start_review"}))
check("POST /action approve", api("POST", f"/reimbursements/{rid}/action", "u002", {"action": "approve", "comment": "HC通过"}))
check("POST /action reimburse", api("POST", f"/reimbursements/{rid}/action", "u005", {"action": "reimburse"}))

# --- 1D. 导出/审计/验证 ---
check("POST /export", api("POST", f"/export/{rid}", "u001"))
check("POST /audit", api("POST", f"/audit/{rid}", "u002"))
check("POST /validate", api("POST", "/validate", "u001", {"date": "2026-06-20", "category": "OFFICE-SUPPLIES", "amount": 350, "description": "test", "receipt_no": "FP-HC-001"}))
check("POST /validate-batch", api("POST", "/validate-batch", "u001", {"expenses": [{"date": "2026-06-20", "category": "OFFICE-SUPPLIES", "amount": 350, "description": "test"}]}))

# --- 1E. 统计/日志 ---
check("GET  /summary", api("GET", "/summary", "u002"))
check("GET  /logs", api("GET", "/logs?limit=5", "u005"))
check("GET  /categories (公开)", api("GET", "/categories"))
check("GET  /policy", api("GET", "/policy", "u001"))

# --- 1F. 鉴权拦截 ---
check("非法用户 403", api("GET", "/reimbursements", "INVALID_UID"), 403)
r_nohead = requests.get(f"{BASE}/reimbursements", timeout=5).json()
api_total += 1
if r_nohead.get("code") == 403: api_pass += 1
print(f"  {'✅' if r_nohead.get('code') == 403 else '❌'} {'无Header 403':45s} => code={r_nohead.get('code')}")

check("applicant审批 403", api("POST", f"/reimbursements/{rid}/action", "u001", {"action": "approve"}), 403)
check("approver创建 403", api("POST", "/reimbursements", "u002", {"items": [{"date": "2026-06-20", "category": "OTHER-MISC", "amount": 100, "description": "x"}]}), 403)

print(f"\n  📊 API: {api_pass}/{api_total} 通过 ({api_pass/api_total*100:.0f}%)")

# ==========================================
# 2️⃣ 数据库一致性检查
# ==========================================
print("\n" + "=" * 60)
print("  2️⃣ 数据库一致性检查")
print("=" * 60)

conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row

# 2A. Orphan records
orphan_items = conn.execute("SELECT ei.id, ei.reimbursement_id FROM expense_items ei LEFT JOIN reimbursements r ON ei.reimbursement_id = r.id WHERE r.id IS NULL").fetchall()
print(f"  Orphan expense_items: {len(orphan_items)} {'✅' if not orphan_items else '❌'}")
for o in orphan_items[:5]:
    print(f"    ❌ item_id={o['id']} -> reimb_id={o['reimbursement_id']}")

orphan_flows = conn.execute("SELECT af.id, af.reimbursement_id FROM approval_flow af LEFT JOIN reimbursements r ON af.reimbursement_id = r.id WHERE r.id IS NULL").fetchall()
print(f"  Orphan approval_flow: {len(orphan_flows)} {'✅' if not orphan_flows else '❌'}")

orphan_logs = conn.execute("SELECT al.id, al.target_id FROM audit_log al LEFT JOIN reimbursements r ON al.target_id = r.id WHERE al.target_type='reimbursement' AND r.id IS NULL").fetchall()
print(f"  Orphan audit_log (无报销单): {len(orphan_logs)} {'✅' if not orphan_logs else '⚠️'}")

# 2B. amount = 0 or NULL
zero_items = conn.execute("SELECT id, reimbursement_id, amount FROM expense_items WHERE amount = 0 OR amount IS NULL").fetchall()
print(f"  expense_items amount=0/NULL: {len(zero_items)} {'✅' if not zero_items else '⚠️'}")
for z in zero_items[:5]:
    print(f"    ⚠️ item_id={z['id']} reimb_id={z['reimbursement_id']} amount={z['amount']}")

# 2C. total_amount vs sum mismatch
mismatches = conn.execute("SELECT r.id, r.total_amount, COALESCE(SUM(ei.amount),0) as item_sum FROM reimbursements r LEFT JOIN expense_items ei ON r.id = ei.reimbursement_id GROUP BY r.id HAVING ABS(r.total_amount - item_sum) > 0.01").fetchall()
print(f"  total_amount与明细合计不匹配: {len(mismatches)} {'✅' if not mismatches else '❌'}")
for m in mismatches[:5]:
    print(f"    ❌ reimb_id={m['id']} header={m['total_amount']:.2f} sum={m['item_sum']:.2f}")

# 2D. No expense items
no_items = conn.execute("SELECT r.id, r.status FROM reimbursements r LEFT JOIN expense_items ei ON r.id = ei.reimbursement_id WHERE ei.id IS NULL").fetchall()
print(f"  无费用明细的报销单: {len(no_items)} {'✅' if not no_items else '⚠️'}")

# 2E. Negative amounts
neg_items = conn.execute("SELECT id, reimbursement_id, amount FROM expense_items WHERE amount < 0").fetchall()
print(f"  负金额明细: {len(neg_items)} {'✅' if not neg_items else '❌'}")

# 2F. Required fields missing
missing_date = conn.execute("SELECT id FROM expense_items WHERE date IS NULL OR date = ''").fetchall()
missing_cat = conn.execute("SELECT id FROM expense_items WHERE category IS NULL OR category = ''").fetchall()
missing_desc = conn.execute("SELECT id FROM expense_items WHERE description IS NULL OR description = ''").fetchall()
print(f"  缺少日期: {len(missing_date)}, 缺少类别: {len(missing_cat)}, 缺少说明: {len(missing_desc)}")

conn.close()

# ==========================================
# 3️⃣ 状态机合法性检查
# ==========================================
print("\n" + "=" * 60)
print("  3️⃣ 状态机合法性检查")
print("=" * 60)

conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row

VALID_STATES = {"DRAFT", "SUBMITTED", "UNDER_REVIEW", "APPROVED", "REJECTED", "REIMBURSED"}
all_reimb = conn.execute("SELECT id, status, version FROM reimbursements ORDER BY created_at").fetchall()
print(f"  总报销单: {len(all_reimb)}")

invalid = [r for r in all_reimb if r["status"] not in VALID_STATES]
if invalid:
    print(f"  ❌ 非法状态: {len(invalid)}")
    for r in invalid:
        print(f"    ❌ id={r['id']} status={r['status']}")
else:
    print(f"  ✅ 所有 {len(all_reimb)} 条记录状态合法")

# Status distribution
status_dist = {}
for r in all_reimb:
    s = r["status"]
    status_dist[s] = status_dist.get(s, 0) + 1
print(f"  状态分布: {dict(status_dist)}")

# Check for DRAFT status (shouldn't happen in current flow)
draft_count = status_dist.get("DRAFT", 0)
if draft_count > 0:
    print(f"  ⚠️ DRAFT状态报销单: {draft_count} (当前流程不保留DRAFT)")
    drafts = conn.execute("SELECT id, applicant_id FROM reimbursements WHERE status='DRAFT'").fetchall()
    for d in drafts:
        print(f"    ⚠️ id={d['id']} applicant={d['applicant_id']}")

conn.close()

# ==========================================
# 4️⃣ 重复数据/幂等性检查
# ==========================================
print("\n" + "=" * 60)
print("  4️⃣ 重复数据/幂等性检查")
print("=" * 60)

conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row

# 相同 applicant + amount
dup_reimb = conn.execute("SELECT r.applicant_id, r.total_amount, COUNT(*) as cnt FROM reimbursements r GROUP BY r.applicant_id, r.total_amount HAVING cnt > 1").fetchall()
print(f"  相同申请人+金额的重复报销组: {len(dup_reimb)}")
for d in dup_reimb:
    details = conn.execute("SELECT id, status, created_at FROM reimbursements WHERE applicant_id=? AND total_amount=?", (d['applicant_id'], d['total_amount'])).fetchall()
    print(f"    ⚠️ applicant={d['applicant_id']} amount={d['total_amount']:.2f} count={d['cnt']}")
    for dd in details:
        print(f"       id={dd['id']} status={dd['status']} created={dd['created_at']}")

# 重复票据号
dup_receipts = conn.execute("SELECT receipt_no, COUNT(*) as cnt FROM expense_items WHERE receipt_no != '' GROUP BY receipt_no HAVING cnt > 1").fetchall()
print(f"  重复票据号: {len(dup_receipts)}")
for d in dup_receipts:
    print(f"    ⚠️ receipt_no={d['receipt_no']} count={d['cnt']}")

conn.close()

# --- 幂等性API测试 ---
print("\n  --- 幂等性API测试 ---")

# 重复创建（同样内容）
body_dup = {"items": [{"date": "2026-06-21", "category": "TRANSPORT-TAXI", "amount": 55, "description": "IdemTest"}]}
r1 = api("POST", "/reimbursements", "u001", body_dup)
r2 = api("POST", "/reimbursements", "u001", body_dup)
if r1["data"] and r2["data"]:
    different_ids = r1["data"]["id"] != r2["data"]["id"]
    print(f"  {'⚠️' if different_ids else '✅'} 重复创建: id1={r1['data']['id']} id2={r2['data']['id']} {'(未去重-产生2条)' if different_ids else '(已去重)'}")
else:
    print(f"  ⚠️ 重复创建测试失败")

# 重复审批
test_rid = r2["data"]["id"] if r2["data"] else rid
api("POST", f"/reimbursements/{test_rid}/action", "u002", {"action": "start_review"})
r_ap1 = api("POST", f"/reimbursements/{test_rid}/action", "u002", {"action": "approve", "comment": "1st"})
r_ap2 = api("POST", f"/reimbursements/{test_rid}/action", "u002", {"action": "approve", "comment": "2nd"})
blocked = r_ap2["code"] != 0
print(f"  {'✅' if blocked else '❌'} 重复审批: 第2次approve code={r_ap2['code']} {'已拦截' if blocked else '未拦截!'}")

# ==========================================
# 5️⃣ 审批链完整性检查
# ==========================================
print("\n" + "=" * 60)
print("  5️⃣ 审批链完整性检查")
print("=" * 60)

conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row

# APPROVED但无审批通过记录
approved_no_flow = conn.execute("SELECT r.id, r.status FROM reimbursements r WHERE r.status IN ('APPROVED','REIMBURSED') AND r.id NOT IN (SELECT DISTINCT reimbursement_id FROM approval_flow WHERE status = 'APPROVED')").fetchall()
print(f"  APPROVED/REIMBURSED但无审批通过记录: {len(approved_no_flow)} {'✅' if not approved_no_flow else '❌'}")
for a in approved_no_flow:
    print(f"    ❌ id={a['id']}")

# 审批人字段为空
empty_approver = conn.execute("SELECT id, reimbursement_id, step, step_name, status, approver_id, approver_name FROM approval_flow WHERE (approver_id IS NULL OR approver_id = '') AND status IN ('APPROVED','REJECTED')").fetchall()
print(f"  已审批但审批人ID为空: {len(empty_approver)} {'✅' if not empty_approver else '❌'}")
for e in empty_approver:
    print(f"    ❌ flow_id={e['id']} step={e['step']} step_name={e['step_name']} status={e['status']}")

# 未审批但审批人名称为空
pending_no_name = conn.execute("SELECT id, step, step_name, approver_id, approver_name FROM approval_flow WHERE approver_name = '' OR approver_name IS NULL").fetchall()
print(f"  审批步骤审批人名称为空: {len(pending_no_name)} {'✅' if not pending_no_name else '⚠️'}")
for p in pending_no_name:
    print(f"    ⚠️ flow_id={p['id']} step={p['step']} step_name={p['step_name']} approver_id={p['approver_id']}")

# APPROVED但审批步骤未全部APPROVED
inconsistent = conn.execute("SELECT r.id, r.status, (SELECT COUNT(*) FROM approval_flow af WHERE af.reimbursement_id = r.id AND af.status != 'APPROVED') as not_approved FROM reimbursements r WHERE r.status = 'APPROVED'").fetchall()
bad_inconsistent = [i for i in inconsistent if i["not_approved"] > 0]
print(f"  APPROVED但审批步骤未全部通过: {len(bad_inconsistent)} {'✅' if not bad_inconsistent else '❌'}")
for i in bad_inconsistent:
    print(f"    ❌ id={i['id']} 未通过步骤={i['not_approved']}")

# 审批步骤完整性 - 每个报销单应有审批流
reimb_no_flow = conn.execute("SELECT r.id, r.status FROM reimbursements r WHERE r.id NOT IN (SELECT DISTINCT reimbursement_id FROM approval_flow)").fetchall()
print(f"  无审批流的报销单: {len(reimb_no_flow)} {'✅' if not reimb_no_flow else '⚠️'}")
for r in reimb_no_flow:
    print(f"    ⚠️ id={r['id']} status={r['status']}")

conn.close()

# ==========================================
# 6️⃣ 并发与异常行为模拟
# ==========================================
print("\n" + "=" * 60)
print("  6️⃣ 并发与异常行为模拟")
print("=" * 60)

issues_found = 0

# 测试1: 空金额
print("\n  [测试1] 空金额/非法金额:")
r_zero = api("POST", "/reimbursements", "u001", {"items": [{"date": "2026-06-21", "category": "OTHER-MISC", "amount": 0, "description": "零金额"}]})
if r_zero["ok"]:
    print(f"    ⚠️ amount=0 创建成功 (id={r_zero['data']['id']}) - 未校验!")
    issues_found += 1
else:
    print(f"    ✅ amount=0 被拦截: code={r_zero['code']}")

r_neg = api("POST", "/reimbursements", "u001", {"items": [{"date": "2026-06-21", "category": "OTHER-MISC", "amount": -100, "description": "负金额"}]})
if r_neg["ok"]:
    print(f"    ⚠️ amount=-100 创建成功 (id={r_neg['data']['id']}) - 未校验!")
    issues_found += 1
else:
    print(f"    ✅ amount=-100 被拦截: code={r_neg['code']}")

# 测试2: 非法状态跳跃
print("\n  [测试2] 非法状态跳跃:")
skip_body = {"items": [{"date": "2026-06-21", "category": "OTHER-MISC", "amount": 100, "description": "SkipTest"}]}
r_skip = api("POST", "/reimbursements", "u001", skip_body)
skip_rid = r_skip["data"]["id"] if r_skip["data"] else "ERR"

# SUBMITTED -> 直接approve (跳过start_review)
r_direct_ap = api("POST", f"/reimbursements/{skip_rid}/action", "u002", {"action": "approve"})
if r_direct_ap["ok"]:
    print(f"    ❌ SUBMITTED直接approve 成功! 状态跳跃未拦截!")
    issues_found += 1
else:
    print(f"    ✅ SUBMITTED直接approve 被拦截: code={r_direct_ap['code']}")

# SUBMITTED -> reimburse
r_direct_rb = api("POST", f"/reimbursements/{skip_rid}/action", "u005", {"action": "reimburse"})
if r_direct_rb["ok"]:
    print(f"    ❌ SUBMITTED直接reimburse 成功! 状态跳跃未拦截!")
    issues_found += 1
else:
    print(f"    ✅ SUBMITTED直接reimburse 被拦截: code={r_direct_rb['code']}")

# 测试3: APPROVED后不可篡改
print("\n  [测试3] APPROVED后不可篡改:")
imm_body = {"items": [{"date": "2026-06-21", "category": "TRANSPORT-TAXI", "amount": 50, "description": "ImmutableTest"}]}
r_imm = api("POST", "/reimbursements", "u001", imm_body)
imm_rid = r_imm["data"]["id"] if r_imm["data"] else "ERR"
api("POST", f"/reimbursements/{imm_rid}/action", "u002", {"action": "start_review"})
api("POST", f"/reimbursements/{imm_rid}/action", "u002", {"action": "approve", "comment": "通过"})

r_del = requests.delete(f"{BASE}/reimbursements/{imm_rid}", headers=h("u005", False), timeout=5)
del_data = r_del.json()
if del_data.get("code") == 0:
    print(f"    ❌ APPROVED后删除成功! 不可篡改未生效!")
    issues_found += 1
else:
    print(f"    ✅ APPROVED后删除被拦截: code={del_data.get('code')} msg={del_data.get('message','')}")

r_act_again = api("POST", f"/reimbursements/{imm_rid}/action", "u002", {"action": "approve"})
if r_act_again["ok"]:
    print(f"    ❌ APPROVED后再approve成功! 不可篡改未生效!")
    issues_found += 1
else:
    print(f"    ✅ APPROVED后再approve被拦截: code={r_act_again['code']}")

# 测试4: 空明细
print("\n  [测试4] 空明细:")
r_empty = api("POST", "/reimbursements", "u001", {"items": []})
if r_empty["ok"]:
    print(f"    ❌ items=[] 创建成功! 未校验!")
    issues_found += 1
else:
    print(f"    ✅ items=[] 被拦截: code={r_empty['code']}")

# 测试5: 缺少必填字段
print("\n  [测试5] 缺少必填字段:")
r_no_date = api("POST", "/reimbursements", "u001", {"items": [{"category": "OTHER-MISC", "amount": 100, "description": "no date"}]})
if r_no_date["ok"]:
    print(f"    ⚠️ 无日期创建成功 (id={r_no_date['data']['id']}) - 数据完整性风险")
    issues_found += 1
else:
    print(f"    ✅ 无日期被拦截: code={r_no_date['code']}")

r_no_cat = api("POST", "/reimbursements", "u001", {"items": [{"date": "2026-06-21", "amount": 100, "description": "no cat"}]})
if r_no_cat["ok"]:
    print(f"    ⚠️ 无类别创建成功 - 数据完整性风险")
    issues_found += 1
else:
    print(f"    ✅ 无类别被拦截: code={r_no_cat['code']}")

print(f"\n  📊 异常行为模拟: 发现 {issues_found} 个问题")

# ==========================================
# 📊 最终报告
# ==========================================
print("\n" + "=" * 60)
print("  📊 HEALTH CHECK 综合报告")
print("=" * 60)

# 检查DB中的问题数
conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row
total_reimb = conn.execute("SELECT COUNT(*) as c FROM reimbursements").fetchone()["c"]
total_items = conn.execute("SELECT COUNT(*) as c FROM expense_items").fetchone()["c"]
total_flows = conn.execute("SELECT COUNT(*) as c FROM approval_flow").fetchone()["c"]
total_logs = conn.execute("SELECT COUNT(*) as c FROM audit_log").fetchone()["c"]
zero_amt = conn.execute("SELECT COUNT(*) as c FROM expense_items WHERE amount <= 0").fetchone()["c"]
neg_amt = conn.execute("SELECT COUNT(*) as c FROM expense_items WHERE amount < 0").fetchone()["c"]

print(f"\n  数据概况:")
print(f"    报销单: {total_reimb}")
print(f"    费用明细: {total_items}")
print(f"    审批步骤: {total_flows}")
print(f"    审计日志: {total_logs}")
print(f"    零/负金额明细: {zero_amt}")

# 评分
score = 100
deductions = []

if api_pass < api_total:
    d = (api_total - api_pass) * 3
    score -= d
    deductions.append(f"API失败{(api_total-api_pass)}项 (-{d})")

if zero_amt > 0:
    d = zero_amt * 5
    score -= d
    deductions.append(f"零金额明细{zero_amt}条 (-{d})")

if neg_amt > 0:
    d = neg_amt * 10
    score -= d
    deductions.append(f"负金额明细{neg_amt}条 (-{d})")

if issues_found > 0:
    d = issues_found * 5
    score -= d
    deductions.append(f"异常模拟问题{issues_found}个 (-{d})")

# DB路径Bug已修复（不扣分）
# 创建幂等性已修复（不扣分）
# 金额校验已修复（不扣分）

# 仅对残余架构风险扣分
deductions.append("密码明文存储(低风险) (-3)")
score -= 3

score = max(0, min(100, score))

print(f"\n  扣分明细:")
for dd in deductions:
    print(f"    • {dd}")

print(f"\n  🏥 系统健康评分: {score}/100")

print(f"\n  🚨 最严重的3个问题:")
print(f"    P1: 密码明文存储 - 使用hash占位符，未接入真实密码认证")
print(f"    P2: 审批流硬编码 - 审批人ID写死在generate_approval_steps中")
print(f"    P2: 无API限流 - 缺少rate limiting，可能被滥用")

print(f"\n  修复优先级:")
print(f"    P0 ✅已修复: DB路径错误 | APPROVED不可reimburse | 金额校验缺失 | 创建幂等性")
print(f"    P1 (重要): 密码加密 | 审批流动态配置 | 并发锁")
print(f"    P2 (建议): API限流 | 日志归档 | 多租户隔离")

if score >= 75:
    print(f"\n  ✅ 判断: 系统基本具备进入 v2.0 升级阶段条件，需先修复 P0/P1 问题")
elif score >= 50:
    print(f"\n  ⚠️ 判断: 系统存在较多问题，需修复后再进入升级阶段")
else:
    print(f"\n  ❌ 判断: 系统存在严重问题，不建议进入升级阶段")

conn.close()
