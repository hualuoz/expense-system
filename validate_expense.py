#!/usr/bin/env python3
"""Validate expense items against reimbursement policy rules."""

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

# Default policy rules
DEFAULT_POLICY = {
    "reimbursement_window_days": 30,
    "receipt_threshold": 200,
    "vat_invoice_threshold": 1000,
    "contract_threshold": 5000,
    "approval_thresholds": [
        {"max": 2000, "levels": ["manager", "finance"]},
        {"max": 10000, "levels": ["manager", "department_head", "finance"]},
        {"max": None, "levels": ["manager", "department_head", "vp", "finance"]},
    ],
    "travel": {
        "accommodation": {
            "tier1": {"staff": 500, "manager": 700, "director": 1000},
            "tier2": {"staff": 400, "manager": 550, "director": 800},
            "tier3": {"staff": 300, "manager": 400, "director": 600},
        },
        "meal_daily": {
            "tier1": 170,
            "tier2": 145,
            "tier3": 120,
        },
    },
    "entertainment": {
        "staff": {"per_event": 1000, "per_person": 150, "annual": 5000},
        "manager": {"per_event": 2000, "per_person": 250, "annual": 15000},
        "director": {"per_event": 5000, "per_person": 400, "annual": 30000},
    },
    "prohibited_items": [
        "personal_consumption",
        "fines",
        "membership_fees",
        "luxury_consumption",
    ],
}

# Valid expense categories
VALID_CATEGORIES = {
    "TRAVEL-TRANSPORT", "TRAVEL-LODGING", "TRAVEL-MEAL", "TRAVEL-OTHER",
    "OFFICE-SUPPLIES", "OFFICE-EQUIPMENT", "OFFICE-SOFTWARE", "OFFICE-PRINT", "OFFICE-POST",
    "ENTERTAIN-MEAL", "ENTERTAIN-GIFT", "ENTERTAIN-OTHER",
    "TRAINING-COURSE", "TRAINING-MATERIAL", "TRAINING-CERT",
    "COMM-MOBILE", "COMM-NETWORK",
    "TRANSPORT-TAXI", "TRANSPORT-PARKING", "TRANSPORT-FUEL", "TRANSPORT-PUBLIC",
    "OTHER-MISC", "OTHER-DONATION",
}

# Tier-1 cities
TIER1_CITIES = {"北京", "上海", "广州", "深圳"}

# Chinese-to-English field name mapping for CSV files
CN_FIELD_MAP = {
    "序号": "index",
    "日期": "date",
    "费用类别": "category",
    "金额": "amount",
    "说明": "description",
    "票据号": "receipt_no",
    "项目编码": "project_code",
    "城市": "city",
    "员工级别": "employee_level",
}


def load_policy(policy_path=None):
    """Load policy from file or return default."""
    if policy_path and Path(policy_path).exists():
        with open(policy_path, "r", encoding="utf-8") as f:
            custom = json.load(f)
            # Merge with defaults
            merged = {**DEFAULT_POLICY, **custom}
            return merged
    return DEFAULT_POLICY


def validate_single_expense(expense, policy, today=None):
    """Validate a single expense item. Returns list of findings."""
    findings = []
    today = today or datetime.now().date()

    # Check required fields
    required_fields = ["date", "category", "amount", "description"]
    for field in required_fields:
        if field not in expense or not expense[field]:
            findings.append({
                "severity": "critical",
                "icon": "🔴",
                "field": field,
                "message": f"缺少必填字段: {field}",
            })

    if findings:
        return findings  # Cannot continue validation without required fields

    # Parse date
    try:
        expense_date = datetime.strptime(str(expense["date"]), "%Y-%m-%d").date()
    except ValueError:
        findings.append({
            "severity": "critical",
            "icon": "🔴",
            "field": "date",
            "message": f"日期格式错误: {expense['date']}，应为 YYYY-MM-DD",
        })
        return findings

    # Check reimbursement window
    window_days = policy["reimbursement_window_days"]
    if (today - expense_date).days > window_days:
        findings.append({
            "severity": "warning",
            "icon": "🟡",
            "field": "date",
            "message": f"费用已超过 {window_days} 天报销时限（费用日期: {expense['date']}）",
        })

    # Check category validity
    category = expense.get("category", "")
    if category not in VALID_CATEGORIES:
        findings.append({
            "severity": "critical",
            "icon": "🔴",
            "field": "category",
            "message": f"无效的费用类别: {category}",
        })

    # Check receipt requirements
    amount = float(expense.get("amount", 0))
    receipt_no = expense.get("receipt_no", "")

    if amount >= policy["receipt_threshold"] and not receipt_no:
        findings.append({
            "severity": "critical",
            "icon": "🔴",
            "field": "receipt_no",
            "message": f"金额 ¥{amount:.2f} ≥ ¥{policy['receipt_threshold']}，须提供发票号",
        })

    if amount >= policy["vat_invoice_threshold"]:
        if receipt_no and not receipt_no.startswith("FP"):
            findings.append({
                "severity": "warning",
                "icon": "🟡",
                "field": "receipt_no",
                "message": f"金额 ¥{amount:.2f} ≥ ¥{policy['vat_invoice_threshold']}，建议提供增值税专用发票",
            })

    # Check approval level
    approval_thresholds = policy["approval_thresholds"]
    required_levels = None
    for threshold in approval_thresholds:
        if threshold["max"] is None or amount <= threshold["max"]:
            required_levels = threshold["levels"]
            break

    if required_levels and len(required_levels) > 2:
        findings.append({
            "severity": "info",
            "icon": "🟢",
            "field": "approval",
            "message": f"金额 ¥{amount:.2f} 需 {len(required_levels)} 级审批: {' → '.join(required_levels)}",
        })

    # Category-specific checks
    if category == "TRAVEL-LODGING":
        city = expense.get("city", "")
        tier = _get_city_tier(city)
        level = expense.get("employee_level", "staff")
        limit = policy["travel"]["accommodation"].get(tier, {}).get(level, 0)
        if limit and amount > limit:
            findings.append({
                "severity": "warning",
                "icon": "🟡",
                "field": "amount",
                "message": f"住宿费 ¥{amount:.2f} 超出 {tier} 标准 ¥{limit}/晚（{level}级）",
            })

    if category == "ENTERTAIN-MEAL":
        level = expense.get("employee_level", "staff")
        ent_rules = policy["entertainment"].get(level, policy["entertainment"]["staff"])
        if amount > ent_rules["per_event"]:
            findings.append({
                "severity": "warning",
                "icon": "🟡",
                "field": "amount",
                "message": f"招待费 ¥{amount:.2f} 超出单次上限 ¥{ent_rules['per_event']}（{level}级）",
            })

    if not findings:
        findings.append({
            "severity": "ok",
            "icon": "✅",
            "field": "all",
            "message": "所有检查通过",
        })

    return findings


def _get_city_tier(city):
    """Determine city tier."""
    if city in TIER1_CITIES:
        return "tier1"
    return "tier3"  # Default to tier3 for unknown cities


def validate_batch(expenses, policy, today=None):
    """Validate a batch of expenses. Check for duplicates across items."""
    all_findings = {}
    receipt_numbers = {}

    for i, expense in enumerate(expenses):
        findings = validate_single_expense(expense, policy, today)
        all_findings[i] = findings

        # Track receipt numbers for duplicate detection
        receipt_no = expense.get("receipt_no", "")
        if receipt_no:
            if receipt_no in receipt_numbers:
                # Add duplicate finding to both items
                dup_msg = f"重复票据号: {receipt_no}（第 {receipt_numbers[receipt_no]+1} 项和第 {i+1} 项）"
                all_findings[i].append({
                    "severity": "critical",
                    "icon": "🔴",
                    "field": "receipt_no",
                    "message": dup_msg,
                })
                all_findings[receipt_numbers[receipt_no]].append({
                    "severity": "critical",
                    "icon": "🔴",
                    "field": "receipt_no",
                    "message": dup_msg,
                })
            else:
                receipt_numbers[receipt_no] = i

    return all_findings


def print_findings(findings, item_index=None):
    """Print validation findings in readable format."""
    prefix = f"第 {item_index + 1} 项" if item_index is not None else ""
    for f in findings:
        print(f"  {f['icon']} [{f['severity'].upper()}] {f['message']}")


def main():
    parser = argparse.ArgumentParser(description="Validate expense items against policy rules")
    parser.add_argument("--date", help="Expense date (YYYY-MM-DD)")
    parser.add_argument("--category", help="Expense category code")
    parser.add_argument("--amount", type=float, help="Expense amount in CNY")
    parser.add_argument("--description", help="Expense description")
    parser.add_argument("--receipt-no", help="Receipt/invoice number")
    parser.add_argument("--city", help="City (for travel expenses)")
    parser.add_argument("--employee-level", default="staff", help="Employee level: staff/manager/director")
    parser.add_argument("--audit", action="store_true", help="Audit mode: validate a batch from input file")
    parser.add_argument("--input", help="Input file (JSON/CSV) for batch validation")
    parser.add_argument("--policy", help="Custom policy JSON file")
    parser.add_argument("--today", help="Override today's date (YYYY-MM-DD)")

    args = parser.parse_args()
    policy = load_policy(args.policy)
    today = datetime.strptime(args.today, "%Y-%m-%d").date() if args.today else datetime.now().date()

    if args.audit:
        # Batch validation mode
        if not args.input:
            print("错误: 审计模式需要 --input 参数指定输入文件")
            sys.exit(1)

        input_path = Path(args.input)
        if not input_path.exists():
            print(f"错误: 文件不存在: {args.input}")
            sys.exit(1)

        if input_path.suffix == ".json":
            with open(input_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                expenses = data if isinstance(data, list) else data.get("expenses", [])
        elif input_path.suffix == ".csv":
            import csv
            expenses = []
            with open(input_path, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    # Map Chinese field names to English
                    mapped = {}
                    for key, val in row.items():
                        eng_key = CN_FIELD_MAP.get(key.strip(), key.strip())
                        mapped[eng_key] = val.strip() if val else ""
                    if "amount" in mapped and mapped["amount"]:
                        try:
                            mapped["amount"] = float(mapped["amount"])
                        except ValueError:
                            mapped["amount"] = 0.0
                    expenses.append(mapped)
        else:
            print(f"错误: 不支持的文件格式: {input_path.suffix}")
            sys.exit(1)

        print(f"=== 报销审计报告 ===")
        print(f"审计日期: {today}")
        print(f"费用笔数: {len(expenses)}")
        print()

        all_findings = validate_batch(expenses, policy, today)
        total_issues = 0

        for idx, findings in all_findings.items():
            issues = [f for f in findings if f["severity"] != "ok"]
            if issues:
                total_issues += len(issues)
                print(f"第 {idx + 1} 项: {expenses[idx].get('description', 'N/A')}")
                print_findings(findings, idx)
                print()

        # Summary
        criticals = sum(1 for findings in all_findings.values() for f in findings if f["severity"] == "critical")
        warnings = sum(1 for findings in all_findings.values() for f in findings if f["severity"] == "warning")
        infos = sum(1 for findings in all_findings.values() for f in findings if f["severity"] == "info")
        oks = sum(1 for findings in all_findings.values() for f in findings if f["severity"] == "ok")

        print("--- 汇总 ---")
        print(f"🔴 严重问题: {criticals}")
        print(f"🟡 警告: {warnings}")
        print(f"🟢 提示: {infos}")
        print(f"✅ 通过: {oks}")

        if criticals > 0:
            print("\n⚠️ 存在严重问题，报销申请须修正后重新提交！")
            sys.exit(1)
        elif warnings > 0:
            print("\n⚠️ 存在警告项，建议确认后提交。")
            sys.exit(0)
        else:
            print("\n✅ 所有检查通过，可以提交报销申请。")
            sys.exit(0)

    else:
        # Single expense validation mode
        if not all([args.date, args.category, args.amount]):
            print("错误: 单项验证需要 --date, --category, --amount 参数")
            sys.exit(1)

        expense = {
            "date": args.date,
            "category": args.category,
            "amount": args.amount,
            "description": args.description or "",
            "receipt_no": args.receipt_no or "",
            "city": args.city or "",
            "employee_level": args.employee_level,
        }

        print("=== 费用验证结果 ===")
        findings = validate_single_expense(expense, policy, today)
        print_findings(findings)

        has_critical = any(f["severity"] == "critical" for f in findings)
        if has_critical:
            sys.exit(1)


if __name__ == "__main__":
    main()
