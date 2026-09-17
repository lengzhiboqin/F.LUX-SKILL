#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
牵牛花数据采集 - 浏览器采集器（基于CDP）
通过 Chrome DevTools Protocol 在已登录的浏览器上下文中执行数据采集
优化：订单按小时切片拉取，避免单次数据量过大
"""

import json
import time
import asyncio
import logging
import websockets
import urllib.request
from pathlib import Path
from datetime import datetime, timedelta


class CDPCollector:
    """基于CDP的牵牛花数据采集器"""

    def __init__(self, port=9222):
        self.port = port
        self.ws = None
        self.msg_id = 0

    def connect(self):
        """连接浏览器，找到牵牛花页面"""
        try:
            resp = urllib.request.urlopen(f"http://127.0.0.1:{self.port}/json")
            pages = json.loads(resp.read().decode())

            qianniu_page = None
            for p in pages:
                if "meituan.com" in p.get("url", ""):
                    qianniu_page = p
                    break

            if not qianniu_page:
                logging.error("未找到牵牛花页面，请确认浏览器已登录")
                return False

            self.ws_url = qianniu_page["webSocketDebuggerUrl"]
            logging.info(f"已连接牵牛花页面: {qianniu_page.get('title', '')}")
            return True
        except Exception as e:
            logging.error(f"浏览器连接失败: {e}")
            return False

    async def __aenter__(self):
        self.ws = await websockets.connect(self.ws_url)
        await self._send("Runtime.enable")
        await self._send("Page.enable")
        return self

    async def __aexit__(self, *args):
        if self.ws:
            await self.ws.close()

    def _next_id(self):
        self.msg_id += 1
        return self.msg_id

    async def _send(self, method, params=None, timeout=30):
        """发送CDP命令并等待响应"""
        msg_id = self._next_id()
        await self.ws.send(json.dumps({
            "id": msg_id,
            "method": method,
            "params": params or {}
        }))

        start = time.time()
        while time.time() - start < timeout:
            try:
                msg = json.loads(await asyncio.wait_for(self.ws.recv(), timeout=timeout))
                if msg.get("id") == msg_id:
                    return msg.get("result", {})
            except asyncio.TimeoutError:
                break
        raise TimeoutError(f"CDP命令超时: {method}")

    async def evaluate(self, expression, await_promise=True, timeout=60):
        """在页面上下文中执行JavaScript"""
        result = await self._send("Runtime.evaluate", {
            "expression": expression,
            "awaitPromise": await_promise,
            "returnByValue": True
        }, timeout=timeout)
        return result.get("result", {}).get("value")

    async def navigate(self, url):
        """导航到指定URL"""
        await self._send("Page.navigate", {"url": url})
        await asyncio.sleep(3)

    # ============================================================
    # 路径1: 订单API采集（按小时切片）
    # ============================================================

    async def collect_orders(self, data_date, output_dir):
        """
        采集订单数据（API方式，按小时切片）
        每小时单独拉取，避免单次数据量过大
        """
        logging.info("开始采集订单数据（按小时切片）...")

        # 计算日期范围（北京时间）
        dt = datetime.strptime(data_date, "%Y-%m-%d")
        day_start_ms = int(dt.timestamp() * 1000) - 8 * 3600000  # 北京时间零点

        all_orders = []
        hour_stats = []

        # 按24小时切片
        for hour in range(24):
            hour_start = day_start_ms + hour * 3600000
            hour_end = hour_start + 3600000 - 1
            now_ms = int(time.time() * 1000)

            # 如果是今天且当前小时还没结束，跳过未来的小时
            today_str = datetime.now().strftime("%Y-%m-%d")
            if data_date == today_str and hour > datetime.now().hour:
                logging.info(f"  小时 {hour:02d}: 尚未到达，跳过")
                continue

            hour_orders = await self._fetch_orders_hour(hour_start, hour_end, now_ms)
            if hour_orders:
                all_orders.extend(hour_orders)
                hour_stats.append(f"{hour:02d}时:{len(hour_orders)}单")
                logging.info(f"  小时 {hour:02d}: {len(hour_orders)}条")
            else:
                logging.info(f"  小时 {hour:02d}: 0条")

            await asyncio.sleep(0.3)  # 避免限流

        # 过滤F.LUX门店
        flux_orders = []
        for order in all_orders:
            store_name = order.get("storeName", "")
            if any(kw.lower() in store_name.lower() for kw in ["F.LUX", "F·LUX"]):
                if "觅初" not in store_name:
                    flux_orders.append(order)

        # 保存结果
        output_file = Path(output_dir) / f"订单API_{data_date}.json"
        result = {
            "data_date": data_date,
            "collect_time": datetime.now().isoformat(),
            "total_orders": len(all_orders),
            "flux_orders": len(flux_orders),
            "hour_stats": hour_stats,
            "orders": flux_orders
        }

        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)

        logging.info(f"订单采集完成: 总{len(all_orders)}条，F.LUX {len(flux_orders)}条 → {output_file}")
        return True, str(output_file), len(flux_orders), None

    async def _fetch_orders_hour(self, start_ms, end_ms, now_ms, max_pages=20):
        """拉取单个小时的订单（支持分页）"""
        orders = []
        page_no = 1
        page_size = 100

        while page_no <= max_pages:
            js_code = f"""
            (async () => {{
                try {{
                    const resp = await fetch('/api/v1/orderfuse/newQueryList', {{
                        method: 'POST',
                        headers: {{'Content-Type': 'application/json'}},
                        credentials: 'include',
                        body: JSON.stringify({{
                            "_t": {now_ms},
                            "createStartTime": {start_ms},
                            "createEndTime": {end_ms},
                            "pageNo": {page_no},
                            "pageSize": {page_size}
                        }})
                    }});
                    return await resp.json();
                }} catch(e) {{ return {{error: e.message}}; }}
            }})()
            """

            try:
                data = await self.evaluate(js_code, timeout=30)
            except Exception as e:
                logging.warning(f"    第{page_no}页请求失败: {e}")
                break

            if not data or data.get("code") != 0:
                logging.warning(f"    第{page_no}页API返回异常: {str(data)[:100]}")
                break

            order_list = data.get("data", {}).get("orderList", [])
            if not order_list:
                break

            orders.extend(order_list)

            if len(order_list) < page_size:
                break

            page_no += 1
            await asyncio.sleep(0.3)

        return orders

    # ============================================================
    # 路径2: 门店经营指标（首页数据API）
    # ============================================================

    async def collect_store_metrics(self, data_date, output_dir):
        """采集门店经营指标"""
        logging.info("开始采集门店经营指标...")

        try:
            # 尝试通过首页已加载的数据获取
            js_code = """
            (async () => {
                try {
                    // 从首页获取门店列表数据
                    const resp = await fetch('/api/v1/home/getHomeData', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        credentials: 'include',
                        body: JSON.stringify({})
                    });
                    return await resp.json();
                } catch(e) { return {error: e.message}; }
            })()
            """
            data = await self.evaluate(js_code, timeout=30)

            if data and not data.get("error"):
                output_file = Path(output_dir) / f"门店经营指标_{data_date}.json"
                with open(output_file, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                logging.info(f"门店经营指标采集完成 → {output_file}")
                return True, str(output_file), 0, None
            else:
                logging.warning("门店经营指标API不可用，待调试")
                return "skipped", None, 0, "API待调试"

        except Exception as e:
            logging.error(f"门店经营指标采集失败: {e}")
            return False, None, 0, str(e)

    # ============================================================
    # 路径3-6: 其他数据路径（待实现完整导出逻辑）
    # ============================================================

    async def collect_store_traffic(self, data_date, output_dir):
        """路径3: 门店流量数据"""
        logging.info("门店流量采集：页面导出逻辑待调试")
        return "skipped", None, 0, "页面导出逻辑待实现"

    async def collect_product_traffic(self, data_date, output_dir):
        """路径4: 商品流量数据"""
        logging.info("商品流量采集：页面导出逻辑待调试")
        return "skipped", None, 0, "页面导出逻辑待实现"

    async def collect_category_analysis(self, data_date, output_dir):
        """路径5: 品类分析数据"""
        logging.info("品类分析采集：页面导出逻辑待调试")
        return "skipped", None, 0, "页面导出逻辑待实现"

    async def collect_store_products(self, data_date, output_dir):
        """路径6: 门店在售商品"""
        logging.info("门店在售商品采集：逐店遍历逻辑待实现")
        return "skipped", None, 0, "逐店遍历逻辑待实现"


# ============================================================
# 同步包装器
# ============================================================

def run_collection(data_date, output_dir, paths=None):
    """运行采集（同步入口）"""
    async def _run():
        collector = CDPCollector()
        if not collector.connect():
            return {"error": "浏览器连接失败"}

        results = {}
        async with collector:
            path_funcs = {
                1: collector.collect_orders,
                2: collector.collect_store_metrics,
                3: collector.collect_store_traffic,
                4: collector.collect_product_traffic,
                5: collector.collect_category_analysis,
                6: collector.collect_store_products,
            }

            selected = paths or [1, 2, 3, 4, 5, 6]
            for path_id in selected:
                if path_id in path_funcs:
                    logging.info(f"\n{'='*50}")
                    logging.info(f"采集路径 {path_id}")
                    logging.info(f"{'='*50}")
                    try:
                        status, filepath, records, error = await path_funcs[path_id](data_date, output_dir)
                        results[path_id] = {
                            "status": status,
                            "file": filepath,
                            "records": records,
                            "error": error
                        }
                    except Exception as e:
                        results[path_id] = {
                            "status": "failed",
                            "file": None,
                            "records": 0,
                            "error": str(e)
                        }

        return results

    return asyncio.run(_run())


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    import sys
    date = sys.argv[1] if len(sys.argv) > 1 else "2026-09-16"
    out = sys.argv[2] if len(sys.argv) > 2 else "/tmp/qianniu_test"
    Path(out).mkdir(parents=True, exist_ok=True)
    results = run_collection(date, out, paths=[1])
    print("\n" + "="*50)
    print("采集结果:")
    print(json.dumps(results, ensure_ascii=False, indent=2))
