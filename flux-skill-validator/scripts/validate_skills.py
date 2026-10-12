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
    """检查通用技能执行状态（优先读last_run.json，其次检查当日产出物）"""
    skill_path = Path(skills_dir) / name
    date_compact = date_str.replace("-", "")

    # 1. 优先读取统一格式的last_run.json
    last_run_file = skill_path / "data" / "last_run.json"
    if last_run_file.exists():
        try:
            with open(last_run_file, encoding="utf-8") as f:
                lr = json.load(f)
            ts = lr.get("timestamp", "")
            if ts and ts[:10].replace("-", "") == date_compact:
                if lr.get("ok"):
                    return "success", None
                else:
                    return "failed", lr.get("error") or "last_run.json标记为失败"
            elif ts:
                return "unknown", f"last_run.json日期={ts[:10]}，非当日{date_str}"
        except Exception as e:
            pass

    # 2. 兜底：检查技能目录下是否有当日修改的非源码文件
    try:
        for f in skill_path.rglob("*"):
            if f.is_file() and f.stat().st_size > 0:
                mtime = datetime.fromtimestamp(f.stat().st_mtime)
                if mtime.strftime("%Y%m%d") == date_compact:
                    if f.suffix not in (".py", ".md", ".sh", ".txt", ".json"):
                        return "success", None
    except Exception:
        pass
    return "unknown", "无当日产出物（需人工确认）"


def check_skill_status(name, skills_dir, date_str):
    """检查单个技能执行状态，返回 (status, error_msg)
    所有技能统一走check_generic_skill（优先读last_run.json）
    """
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


def check_ready_for_main(status, repo_dir=None):
    """检查哪些技能可以合并到main

    v2（修复漏洞）：不再依赖 status 里可能为空的 current_branch（它只在"本次同步有
    代码变化"时才被写入），改为直接检查本地/远程是否存在对应 dev/<name> 分支。
    只要连续>=3天无报错、未合并、且存在 dev 分支，即进入 ready 队列。
    """
    ready = []
    for name, s in status.items():
        days = s.get("consecutive_success_days", 0)
        # 确保转为整数（可能是字符串）
        try:
            days = int(days)
        except (ValueError, TypeError):
            days = 0
        promoted = s.get("promoted_to_main", "")
        if days < 3 or promoted:
            continue
        # 修复：分支存在性用真实 git 判断（本地 ref 或远程 ref），不再看 status.current_branch
        has_dev = False
        if repo_dir:
            for ref in (f"refs/heads/dev/{name}", f"refs/remotes/origin/dev/{name}"):
                try:
                    r = subprocess.run(
                        ["git", "-C", repo_dir, "show-ref", "--quiet", ref],
                        capture_output=True, text=True)
                    if r.returncode == 0:
                        has_dev = True
                        break
                except Exception:
                    pass
        if has_dev:
            ready.append(name)
        else:
            log(f"  ⚠️  {name} 连续{days}天无报错但未找到 dev/{name} 分支，跳过")
    return ready


def auto_merge_to_main(repo_dir, name):
    """把技能 dev 分支的最新内容自动合并到 main。

    用「目录抽取法」：只 checkout dev/<name>/ 目录到 main 工作区并提交，而不是
    整分支 merge——避免把 dev 分支线性历史里曾出现的大文件（如 flux.db）或其他
    技能文件带进 main。合并成功后 push origin main。
    """
    import datetime as _dt
    try:
        # 1. 切到 main
        r = subprocess.run(["git", "-C", repo_dir, "checkout", "main"],
                           capture_output=True, text=True, timeout=120)
        if r.returncode != 0:
            log(f"    checkout main 失败: {r.stderr.strip()[:200]}")
            return False

        # 2. 抽取技能目录（若目录在 main 已存在则覆盖更新）
        r = subprocess.run(["git", "-C", repo_dir, "checkout", f"dev/{name}", "--", f"{name}/"],
                           capture_output=True, text=True, timeout=120)
        if r.returncode != 0:
            log(f"    checkout dev/{name} -- {name}/ 失败: {r.stderr.strip()[:200]}")
            return False
        subprocess.run(["git", "-C", repo_dir, "add", "-A"], capture_output=True, text=True, timeout=60)

        # 3. 移除业务数据/生成产物（不入库）
        r = subprocess.run(
            ["git", "-C", repo_dir, "ls-files", "--cached"],
            capture_output=True, text=True, timeout=60)
        for f in r.stdout.splitlines():
            if f.endswith(".db") or "/_data/" in f or f"{name}/output/" in f:
                subprocess.run(["git", "-C", repo_dir, "rm", "--cached", f],
                               capture_output=True, text=True, timeout=30)

        # 4. 若无实际改动则不提交
        r = subprocess.run(["git", "-C", repo_dir, "status", "--porcelain"],
                           capture_output=True, text=True, timeout=30)
        if not r.stdout.strip():
            log(f"    {name} 目录在 main 与 dev 无差异，无需提交")
            return True

        # 5. 提交
        days = ""
        try:
            import json as _json, os as _os
            sf = _os.path.expanduser("~/.config/flux-skills/skill_status.json")
            with open(sf, encoding="utf-8") as f:
                days = str(_json.load(f).get(name, {}).get("consecutive_success_days", ""))
        except Exception:
            pass
        msg = f"promote: {name} 自动合并到main（连续{days}天无报错验证通过）" if days else \
              f"promote: {name} 自动合并到main"
        subprocess.run(["git", "-C", repo_dir, "commit", "-m", msg],
                       capture_output=True, text=True, timeout=60)

        # 6. push
        r = subprocess.run(["git", "-C", repo_dir, "push", "origin", "main"],
                           capture_output=True, text=True, timeout=180)
        if r.returncode != 0:
            log(f"    push 失败: {r.stderr.strip()[:300]}")
            return False
        return True
    except Exception as e:
        log(f"    auto_merge_to_main 异常: {e}")
        return False


def send_wecom_notification(result, chat_id=None):
    """发送企微通知（优化版格式，美观易读）"""
    try:
        if not Path("/usr/local/bin/wecom-cli").exists() and not Path("/usr/bin/wecom-cli").exists():
            r = subprocess.run(["which", "wecom-cli"], capture_output=True, text=True)
            if r.returncode != 0:
                log("  ⚠️  wecom-cli未安装，跳过通知")
                return False
    except Exception:
        pass

    # 构建通知内容（企微markdown单换行会粘连，必须用空行分隔）
    L = []

    # 标题
    L.append("**老大，技能质量门禁检测完成**")
    L.append("")
    L.append("")

    # 📊 检测概览
    L.append("📊 **检测概览**")
    L.append("")
    L.append(f"· 检测技能：{result['total_skills']}个")
    L.append("")
    L.append(f"· ✅ 成功：{result['success']}个")
    L.append("")
    L.append(f"· ❌ 失败：{result['failed']}个")
    L.append("")
    L.append(f"· ❓ 未知：{result['unknown']}个")
    L.append("")
    L.append("")

    # 🆕 新技能
    if result["new_skills"]:
        L.append("🆕 **新技能自动加入**")
        L.append("")
        for s in result["new_skills"]:
            L.append(f"· {s}")
            L.append("")
        L.append("")

    # ✅ 可合并main（v3：自动合并）
    if result.get("auto_merged"):
        L.append("✅ **已自动合并到main（连续3天无报错）**")
        L.append("")
        for s in result["auto_merged"]:
            L.append(f"· {s}")
            L.append("")
        L.append("")
    elif result["ready_for_main"]:
        L.append("✅ **可合并main（连续3天无报错）**")
        L.append("")
        for s in result["ready_for_main"]:
            L.append(f"· {s}")
            L.append("")
        L.append("")

    # ⚠️ 报错Issue
    if result["issues_created"]:
        L.append("⚠️ **报错Issue（已创建）**")
        L.append("")
        for issue in result["issues_created"]:
            L.append(f"· {issue.get('skill', '')}：{issue.get('url', '')}")
            L.append("")
        L.append("")

    # 📋 各技能状态（分行显示，每个技能一行）
    L.append("📋 **各技能状态**")
    L.append("")
    for s in result["skills"]:
        icon = {"success": "✅", "failed": "❌", "unknown": "❓"}.get(s["status"], "❓")
        status_text = {"success": "成功", "failed": "失败", "unknown": "未知"}.get(s["status"], "未知")
        L.append(f"{icon} {s['name']}：{status_text}（连续{s['consecutive_days']}天无报错）")
        L.append("")

    text = "\n".join(L)

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

    # 计算日期：默认检测当天（T），因为采集/简报都是当天执行的
    if args.date:
        date_str = args.date.replace("-", "")
    else:
        today = datetime.now()
        date_str = today.strftime("%Y%m%d")
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
            # 确保consecutive_success_days是int类型
            try:
                cur_days = int(s.get("consecutive_success_days", 0))
            except (ValueError, TypeError):
                cur_days = 0
            if run_status == "success":
                s["consecutive_success_days"] = cur_days + 1
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

    # 7. 检查可合并main（v2：用真实 git 分支判断，修复 branch="" 漏判）
    ready_for_main = check_ready_for_main(status, repo_dir)
    if ready_for_main:
        log("")
        log(f"=== 可合并main: {', '.join(ready_for_main)} ===")

    # 7b. 自动合并到main（v3：连续达标自动合并，不再只提醒）
    auto_merged = []
    if args.sync and repo_dir and not args.dry_run and ready_for_main:
        log("")
        log("=== 自动合并到main（连续达标自动执行）===")
        for name in ready_for_main:
            ok = auto_merge_to_main(repo_dir, name)
            if ok:
                auto_merged.append(name)
                status[name]["promoted_to_main"] = datetime.now().strftime("%Y-%m-%d %H:%M")
                status[name]["current_branch"] = "main"
                log(f"  ✅ {name} 已自动合并到main")
            else:
                log(f"  ⚠️  {name} 自动合并失败（详见上方）")

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
        "auto_merged": auto_merged,
        "issues_created": issues_created,
        "skills": result_skills,
    }

    # 10. 企微通知
    if args.notify and not args.dry_run:
        log("")
        log("=== 企微通知 ===")
        send_wecom_notification(result)

    # 写统一格式的last_run.json（供自身检测运行状态）
    try:
        script_dir = Path(__file__).parent
        last_run_file = script_dir.parent / "data" / "last_run.json"
        last_run_file.parent.mkdir(parents=True, exist_ok=True)
        last_run = {
            "ok": True,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "data_date": date_display,
            "exit_code": 0,
            "error": None,
            "outputs": [args.status_file],
        }
        last_run_file.write_text(json.dumps(last_run, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        log(f"⚠️ 写last_run.json失败: {e}")

    # 输出JSON结果
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
