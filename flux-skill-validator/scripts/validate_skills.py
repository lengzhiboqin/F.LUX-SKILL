#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""F.LUX 技能质量门禁与GitHub同步主脚本。

用法：
  python3 validate_skills.py --sync --notify          # 每日检测+同步+通知
  python3 validate_skills.py --status-only             # 只查看状态
  python3 validate_skills.py --dry-run                 # 干跑
"""
import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path


def log(msg):
    print(f"[validate] {msg}", file=sys.stderr)


def discover_skills(skills_dir):
    """自动发现所有带SKILL.md的技能目录"""
    skills = []
    for d in sorted(Path(skills_dir).iterdir()):
        if d.is_dir() and (d / "SKILL.md").exists():
            skills.append(d.name)
    return skills


def load_status(status_file):
    """加载技能状态文件"""
    p = Path(status_file).expanduser()
    if p.exists():
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_status(status_file, status):
    """保存技能状态文件"""
    p = Path(status_file).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(status, f, ensure_ascii=False, indent=2)


def get_skill_status(status, name):
    """获取单个技能状态，不存在则初始化"""
    if name not in status:
        status[name] = {
            "current_branch": "",
            "consecutive_success_days": 0,
            "last_run_date": "",
            "last_run_status": "unknown",
            "last_error": None,
            "promoted_to_main": "",
        }
    return status[name]


def check_shangou_pipeline(skills_dir, date_str):
    """检查 shangou-data-pipeline 执行状态"""
    skill_path = Path(skills_dir) / "shangou-data-pipeline"
    # 检查 last_pipeline.json
    possible_paths = [
        skill_path / "data" / "master" / "last_pipeline.json",
        Path("/runtime/user_skills/shangou-data-pipeline/data/master/last_pipeline.json"),
    ]
    for p in possible_paths:
        if p.exists():
            try:
                with open(p, encoding="utf-8") as f:
                    data = json.load(f)
                exit_code = data.get("exit_code", -1)
                run_date = data.get("date", "")
                # 检查是否是当日数据
                if date_str in str(run_date) or date_str.replace("-", "") in str(run_date):
                    if exit_code == 0:
                        return "success", None
                    else:
                        return "failed", f"exit_code={exit_code}, 详情见last_pipeline.json"
                else:
                    return "unknown", f"last_pipeline.json日期={run_date}，非当日{date_str}"
            except Exception as e:
                return "failed", f"解析last_pipeline.json失败: {e}"
    return "unknown", "未找到last_pipeline.json"


def check_flux_daily_briefing(skills_dir, date_str):
    """检查 flux-daily-briefing 执行状态"""
    skill_path = Path(skills_dir) / "flux-daily-briefing"
    date_compact = date_str.replace("-", "")
    # 检查输出目录是否有当日报简文件
    possible_dirs = [
        skill_path / "output",
        Path("/tmp/flux_03/output"),
        Path("/tmp/flux_briefing"),
    ]
    for d in possible_dirs:
        if d.exists():
            for f in d.iterdir():
                if date_compact in f.name and f.name.endswith(".md") and f.stat().st_size > 0:
                    return "success", None
    return "unknown", "未找到当日报简文件"


def check_generic_skill(skills_dir, name, date_str):
    """检查通用技能执行状态（检查是否有当日产出物或日志）"""
    skill_path = Path(skills_dir) / name
    date_compact = date_str.replace("-", "")
    # 检查技能目录下是否有当日修改的文件（排除SKILL.md和脚本）
    try:
        for f in skill_path.rglob("*"):
            if f.is_file() and f.stat().st_size > 0:
                mtime = datetime.fromtimestamp(f.stat().st_mtime)
                if mtime.strftime("%Y%m%d") == date_compact:
                    # 排除源码文件（.py/.md/.sh/.txt），只看产出物
                    if f.suffix not in (".py", ".md", ".sh", ".txt", ".json"):
                        return "success", None
    except Exception:
        pass
    return "unknown", "无当日产出物（需人工确认）"


def check_skill_status(name, skills_dir, date_str):
    """检查单个技能执行状态，返回 (status, error_msg)"""
    if name == "shangou-data-pipeline":
        return check_shangou_pipeline(skills_dir, date_str)
    elif name == "flux-daily-briefing":
        return check_flux_daily_briefing(skills_dir, date_str)
    else:
        return check_generic_skill(skills_dir, name, date_str)


def create_github_issue(repo_dir, skill_name, title, body):
    """创建GitHub Issue"""
    try:
        result = subprocess.run(
            ["gh", "issue", "create",
             "--repo", "lengzhiboqin/F.LUX-SKILL",
             "--title", f"[{skill_name}] {title}",
             "--body", body,
             "--label", "技能优化建议"],
            capture_output=True, text=True, timeout=30,
            cwd=repo_dir,
        )
        if result.returncode == 0:
            url = result.stdout.strip()
            log(f"  ✅ Issue已创建: {url}")
            return url
        else:
            log(f"  ⚠️  Issue创建失败: {result.stderr[:200]}")
            return None
    except Exception as e:
        log(f"  ⚠️  Issue创建异常: {e}")
        return None


def run_github_sync(repo_dir):
    """运行GitHub同步（sync_skills.sh）"""
    sync_script = Path(repo_dir) / "sync_skills.sh"
    if not sync_script.exists():
        log(f"  ⚠️  未找到sync_skills.sh: {sync_script}")
        return False, []
    try:
        result = subprocess.run(
            ["bash", str(sync_script)],
            capture_output=True, text=True, timeout=120,
            cwd=repo_dir,
        )
        output = result.stdout + result.stderr
        # 解析哪些技能被同步了
        synced = []
        for line in output.split("\n"):
            if "已推送到" in line and "dev/" in line:
                # 提取技能名
                for part in line.split():
                    if part.startswith("dev/"):
                        synced.append(part.replace("dev/", ""))
        log(f"  ✅ GitHub同步完成，同步技能: {synced if synced else '无变化'}")
        return True, synced
    except Exception as e:
        log(f"  ⚠️  GitHub同步失败: {e}")
        return False, []


def check_ready_for_main(status):
    """检查哪些技能可以合并到main"""
    ready = []
    for name, s in status.items():
        days = s.get("consecutive_success_days", 0)
        branch = s.get("current_branch", "")
        promoted = s.get("promoted_to_main", "")
        if days >= 3 and branch.startswith("dev/") and not promoted:
            ready.append(name)
    return ready


def send_wecom_notification(result, chat_id=None):
    """发送企微通知（如果wecom-cli可用）"""
    try:
        if not Path("/usr/local/bin/wecom-cli").exists() and not Path("/usr/bin/wecom-cli").exists():
            # 尝试which
            r = subprocess.run(["which", "wecom-cli"], capture_output=True, text=True)
            if r.returncode != 0:
                log("  ⚠️  wecom-cli未安装，跳过通知")
                return False
    except Exception:
        pass

    # 构建通知内容
    lines = []
    lines.append("**老大，技能质量门禁检测完成**")
    lines.append("")
    lines.append(f"📊 检测技能：{result['total_skills']}个（成功{result['success']}，失败{result['failed']}，未知{result['unknown']}）")

    if result["new_skills"]:
        lines.append(f"🆕 新技能自动加入：{'、'.join(result['new_skills'])}")

    if result["ready_for_main"]:
        lines.append(f"✅ 可合并main：{'、'.join(result['ready_for_main'])}（连续3天无报错）")

    if result["issues_created"]:
        lines.append(f"⚠️ 报错Issue：{len(result['issues_created'])}个")

    lines.append("")
    lines.append("各技能状态：")
    for s in result["skills"]:
        icon = {"success": "✅", "failed": "❌", "unknown": "❓"}.get(s["status"], "❓")
        lines.append(f"  {icon} {s['name']}：连续{s['consecutive_days']}天无报错")

    text = "\n".join(lines)

    try:
        chat = chat_id or "woeC4LQgAAk8Ci2f7Xrp32fZ6x5R4YHA"
        content_json = json.dumps({"content": text}, ensure_ascii=False)
        r = subprocess.run(
            ["wecom-cli", "message", "aibot", "send",
             "--chat-id", chat, "--msg-type", "markdown",
             "--markdown", content_json],
            capture_output=True, text=True, timeout=30,
        )
        if r.returncode == 0:
            log("  ✅ 企微通知已发送")
            return True
        else:
            log(f"  ⚠️  企微通知失败: {r.stderr[:200]}")
            return False
    except Exception as e:
        log(f"  ⚠️  企微通知异常: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description="F.LUX技能质量门禁与GitHub同步")
    parser.add_argument("--skills-dir", default=os.path.expanduser("~/.doubao/agent_mode/workspace/.user_skills"))
    parser.add_argument("--repo", default="")
    parser.add_argument("--status-file", default="~/.config/flux-skills/skill_status.json")
    parser.add_argument("--date", default="", help="数据日期YYYYMMDD，默认昨日")
    parser.add_argument("--sync", action="store_true", help="执行GitHub同步")
    parser.add_argument("--notify", action="store_true", help="发送企微通知")
    parser.add_argument("--status-only", action="store_true", help="只查看状态，不更新")
    parser.add_argument("--dry-run", action="store_true", help="干跑模式")
    args = parser.parse_args()

    # 计算日期
    if args.date:
        date_str = args.date.replace("-", "")
    else:
        yesterday = datetime.now() - timedelta(days=1)
        date_str = yesterday.strftime("%Y%m%d")
    date_display = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}"

    # 自动检测仓库路径
    repo_dir = args.repo
    if not repo_dir:
        candidates = [
            "/home/user/Doubao/chats/38439034639855618/F.LUX-SKILL",
            str(Path.home() / "F.LUX-SKILL"),
        ]
        for c in candidates:
            if Path(c).exists():
                repo_dir = c
                break

    log(f"日期: {date_display}")
    log(f"技能目录: {args.skills_dir}")
    log(f"仓库目录: {repo_dir or '未检测到'}")
    log(f"模式: {'干跑' if args.dry_run else ('只查看' if args.status_only else ('检测+同步' if args.sync else '只检测'))}")
    log("")

    # 1. 自动发现技能
    skills = discover_skills(args.skills_dir)
    log(f"发现 {len(skills)} 个技能: {', '.join(skills)}")

    # 2. 加载状态
    status = load_status(args.status_file)
    new_skills = [s for s in skills if s not in status]
    if new_skills:
        log(f"新技能: {', '.join(new_skills)}")

    # 3. 只查看状态模式
    if args.status_only:
        result_skills = []
        for name in skills:
            s = get_skill_status(status, name)
            result_skills.append({
                "name": name,
                "status": s.get("last_run_status", "unknown"),
                "consecutive_days": s.get("consecutive_success_days", 0),
                "branch": s.get("current_branch", ""),
                "promoted": bool(s.get("promoted_to_main", "")),
            })
        print(json.dumps({
            "date": date_display,
            "total_skills": len(skills),
            "new_skills": new_skills,
            "skills": result_skills,
        }, ensure_ascii=False, indent=2))
        return

    # 4. 检查各技能执行状态
    log("")
    log("=== 检查执行状态 ===")
    result_skills = []
    failed_skills = []
    success_count = 0
    failed_count = 0
    unknown_count = 0

    for name in skills:
        s = get_skill_status(status, name)
        run_status, error_msg = check_skill_status(name, args.skills_dir, date_display)
        log(f"  {name}: {run_status}" + (f" ({error_msg})" if error_msg and run_status != "success" else ""))

        if not args.dry_run:
            s["last_run_date"] = date_display
            s["last_run_status"] = run_status
            if run_status == "success":
                s["consecutive_success_days"] = s.get("consecutive_success_days", 0) + 1
                s["last_error"] = None
                success_count += 1
            elif run_status == "failed":
                s["consecutive_success_days"] = 0
                s["last_error"] = error_msg
                failed_count += 1
                failed_skills.append((name, error_msg))
            else:  # unknown
                unknown_count += 1

        result_skills.append({
            "name": name,
            "status": run_status,
            "consecutive_days": s.get("consecutive_success_days", 0),
            "branch": s.get("current_branch", ""),
            "promoted": bool(s.get("promoted_to_main", "")),
        })

    # 5. 报错建Issue
    log("")
    log("=== 报错建Issue ===")
    issues_created = []
    if failed_skills and not args.dry_run:
        for name, error in failed_skills:
            title = f"执行报错 - {date_display}"
            body = f"## 报错信息\n\n{error}\n\n## 复现步骤\n\n1. 执行技能 {name}\n2. 日期 {date_display}\n3. 报错如上\n\n## 影响范围\n\n待确认\n\n---\n*由 flux-skill-validator 自动创建*"
            url = create_github_issue(repo_dir, name, title, body)
            if url:
                issues_created.append({"skill": name, "url": url})

    # 6. GitHub同步
    log("")
    log("=== GitHub同步 ===")
    synced_to_dev = []
    if args.sync and repo_dir and not args.dry_run:
        ok, synced = run_github_sync(repo_dir)
        synced_to_dev = synced
        # 同步后更新分支信息
        for name in synced:
            if name in status:
                status[name]["current_branch"] = f"dev/{name}"
                # 代码有变化，重置验证天数（如果之前是main的话）
                if status[name].get("promoted_to_main"):
                    log(f"  ⚠️  {name} 有代码变化，已从main退回dev，验证天数重置")
                    status[name]["promoted_to_main"] = ""
                    status[name]["consecutive_success_days"] = 0

    # 7. 检查可合并main
    ready_for_main = check_ready_for_main(status)
    if ready_for_main:
        log("")
        log(f"=== 可合并main: {', '.join(ready_for_main)} ===")

    # 8. 保存状态
    if not args.dry_run:
        save_status(args.status_file, status)
        log("")
        log(f"状态已保存: {args.status_file}")

    # 9. 构建结果
    result = {
        "date": date_display,
        "total_skills": len(skills),
        "success": success_count,
        "failed": failed_count,
        "unknown": unknown_count,
        "new_skills": new_skills,
        "synced_to_dev": synced_to_dev,
        "ready_for_main": ready_for_main,
        "issues_created": issues_created,
        "skills": result_skills,
    }

    # 10. 企微通知
    if args.notify and not args.dry_run:
        log("")
        log("=== 企微通知 ===")
        send_wecom_notification(result)

    # 输出JSON结果
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
