#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
牵牛花数据采集 - 页面表格提取器
直接从页面DOM提取带分页的表格数据，比页面导出更可靠
"""

import json
import time
import asyncio
import logging
import websockets
import urllib.request
from pathlib import Path
from datetime import datetime


class TableExtractor:
    """从牵牛花页面提取表格数据"""

    def __init__(self, port=9222):
        self.port = port
        self.ws = None
        self.msg_id = 0

    def connect(self):
        """连接浏览器"""
        try:
            resp = urllib.request.urlopen(f"http://127.0.0.1:{self.port}/json")
            pages = json.loads(resp.read().decode())
            qianniu_page = next((p for p in pages if "meituan.com" in p.get("url", "")), None)
            if not qianniu_page:
                logging.error("未找到牵牛花页面")
                return False
            self.ws_url = qianniu_page["webSocketDebuggerUrl"]
            logging.info(f"已连接: {qianniu_page.get('title', '')}")
            return True
        except Exception as e:
            logging.error(f"连接失败: {e}")
            return False

    async def __aenter__(self):
        self.ws = await websockets.connect(self.ws_url)
        await self._send("Runtime.enable")
        return self

    async def __aexit__(self, *args):
        if self.ws:
            await self.ws.close()

    async def _send(self, method, params=None, timeout=30):
        self.msg_id += 1
        await self.ws.send(json.dumps({"id": self.msg_id, "method": method, "params": params or {}}))
        start = time.time()
        while time.time() - start < timeout:
            try:
                msg = json.loads(await asyncio.wait_for(self.ws.recv(), timeout=timeout))
                if msg.get("id") == self.msg_id:
                    return msg.get("result", {})
            except asyncio.TimeoutError:
                break
        raise TimeoutError(f"超时: {method}")

    async def evaluate(self, expression, timeout=30):
        result = await self._send("Runtime.evaluate", {
            "expression": expression,
            "awaitPromise": True,
            "returnByValue": True
        }, timeout=timeout)
        return result.get("result", {}).get("value")

    async def navigate(self, url):
        await self._send("Page.navigate", {"url": url})
        await asyncio.sleep(5)

    async def extract_table_with_pagination(self, table_index=0, max_pages=10):
        """
        提取指定索引的表格数据，支持分页
        先回到第1页，然后逐页提取
        返回: 表头 + 所有数据行
        """
        # 先回到第1页
        await self._go_to_first_page(table_index)
        await asyncio.sleep(1)

        all_rows = []
        headers = None
        last_first_seq = None

        for page in range(1, max_pages + 1):
            # 提取当前页表格数据
            js = f"""
            (() => {{
                const tables = document.querySelectorAll('table');
                if (!tables[{table_index}]) return JSON.stringify({{error: '表格不存在'}});
                const rows = tables[{table_index}].querySelectorAll('tr');
                const data = [];
                rows.forEach(row => {{
                    const cells = Array.from(row.querySelectorAll('th, td')).map(c => c.textContent.trim());
                    if (cells.some(c => c)) data.push(cells);
                }});
                return JSON.stringify({{data}});
            }})()
            """
            result = await self.evaluate(js)
            if not result:
                break

            data = json.loads(result)
            if data.get("error"):
                logging.warning(f"表格{table_index}第{page}页: {data['error']}")
                break

            rows = data.get("data", [])
            if not rows:
                break

            if page == 1:
                headers = rows[0]
                data_rows = rows[1:]
            else:
                data_rows = [r for r in rows if r != headers]

            # 获取当前页第一条数据的序号
            current_first_seq = data_rows[0][0] if data_rows else None

            # 如果序号和上一页相同，说明已经到最后一页（没有翻页成功）
            if page > 1 and current_first_seq == last_first_seq:
                logging.info(f"  表格{table_index} 已到最后一页，停止")
                break

            last_first_seq = current_first_seq
            all_rows.extend(data_rows)
            logging.info(f"  表格{table_index} 第{page}页: {len(data_rows)}条, 累计{len(all_rows)}条 (首条序号:{current_first_seq})")

            # 点击下一页
            clicked = await self._click_next_page(table_index)
            if not clicked:
                break
            await asyncio.sleep(2)

        return headers, all_rows

    async def _click_next_page(self, table_index):
        """点击下一页按钮 - 根据表格位置找到最近的分页控件"""
        js = f"""
        (() => {{
            const tables = document.querySelectorAll('table');
            const table = tables[{table_index}];
            if (!table) return false;

            const tableRect = table.getBoundingClientRect();
            const tableBottom = tableRect.bottom;

            // 找到所有分页控件，选择位置在表格下方且最近的那个
            const allPaginations = document.querySelectorAll('[class*="ant-pagination"], [class*="pagination"]');
            let bestPagination = null;
            let bestDistance = Infinity;

            allPaginations.forEach(p => {{
                const rect = p.getBoundingClientRect();
                if (rect.width > 0 && rect.height > 0 && rect.top >= tableBottom - 50) {{
                    const distance = rect.top - tableBottom;
                    if (distance >= 0 && distance < bestDistance) {{
                        bestDistance = distance;
                        bestPagination = p;
                    }}
                }}
            }});

            if (!bestPagination) {{
                // 兜底：取所有可见分页控件中最后一个
                const visible = Array.from(allPaginations).filter(p => p.offsetParent !== null);
                bestPagination = visible[visible.length - 1];
            }}

            if (!bestPagination) return false;

            // 方法1: 点击"下一页"按钮
            const nextBtn = bestPagination.querySelector('[class*="next"]');
            if (nextBtn && !nextBtn.classList.contains('disabled') && !nextBtn.classList.contains('ant-pagination-disabled')) {{
                nextBtn.click();
                return true;
            }}

            // 方法2: 点击当前页+1的页码
            const activeItem = bestPagination.querySelector('[class*="item-active"], [class*="active"]');
            if (activeItem) {{
                const nextNum = parseInt(activeItem.textContent) + 1;
                if (!isNaN(nextNum)) {{
                    const items = bestPagination.querySelectorAll('li');
                    for (const item of items) {{
                        if (item.textContent.trim() === String(nextNum)) {{
                            item.click();
                            return true;
                        }}
                    }}
                }}
            }}

            return false;
        }})()
        """
        return await self.evaluate(js)

    async def _go_to_first_page(self, table_index):
        """回到第1页"""
        js = f"""
        (() => {{
            const tables = document.querySelectorAll('table');
            const table = tables[{table_index}];
            if (!table) return false;

            const tableRect = table.getBoundingClientRect();
            const tableBottom = tableRect.bottom;

            // 找到最近的分页控件
            const allPaginations = document.querySelectorAll('[class*="ant-pagination"], [class*="pagination"]');
            let bestPagination = null;
            let bestDistance = Infinity;

            allPaginations.forEach(p => {{
                const rect = p.getBoundingClientRect();
                if (rect.width > 0 && rect.height > 0 && rect.top >= tableBottom - 50) {{
                    const distance = rect.top - tableBottom;
                    if (distance >= 0 && distance < bestDistance) {{
                        bestDistance = distance;
                        bestPagination = p;
                    }}
                }}
            }});

            if (!bestPagination) return false;

            // 点击第1页
            const items = bestPagination.querySelectorAll('li');
            for (const item of items) {{
                if (item.textContent.trim() === '1') {{
                    item.click();
                    return true;
                }}
            }}
            return false;
        }})()
        """
        return await self.evaluate(js)

    async def extract_home_tables(self, output_dir, data_date):
        """提取首页所有表格数据"""
        logging.info("导航到首页...")
        await self.navigate("https://qnh.meituan.com/home.html#/data/home/new")
        await asyncio.sleep(3)

        results = {}

        # 表格0: 门店明细
        logging.info("提取门店明细表格...")
        headers, rows = await self.extract_table_with_pagination(0)
        if headers:
            results["store_metrics"] = {"headers": headers, "rows": rows}
            logging.info(f"  门店明细: {len(rows)}条")

        # 表格1: 商品明细
        logging.info("提取商品明细表格...")
        headers, rows = await self.extract_table_with_pagination(1)
        if headers:
            results["product_metrics"] = {"headers": headers, "rows": rows}
            logging.info(f"  商品明细: {len(rows)}条")

        # 表格2: 客户明细
        logging.info("提取客户明细表格...")
        headers, rows = await self.extract_table_with_pagination(2)
        if headers:
            results["customer_metrics"] = {"headers": headers, "rows": rows}
            logging.info(f"  客户明细: {len(rows)}条")

        # 保存
        output_file = Path(output_dir) / f"首页表格_{data_date}.json"
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)

        logging.info(f"首页表格数据已保存: {output_file}")
        return results


def run_extraction(data_date, output_dir):
    """运行表格提取"""
    async def _run():
        extractor = TableExtractor()
        if not extractor.connect():
            return {"error": "连接失败"}

        async with extractor:
            return await extractor.extract_home_tables(output_dir, data_date)

    return asyncio.run(_run())


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    import sys
    date = sys.argv[1] if len(sys.argv) > 1 else "2026-09-17"
    out = sys.argv[2] if len(sys.argv) > 2 else "/tmp/qianniu_tables"
    Path(out).mkdir(parents=True, exist_ok=True)
    results = run_extraction(date, out)
    print("\n" + "=" * 50)
    print("提取结果:")
    for key, value in results.items():
        if isinstance(value, dict) and "rows" in value:
            print(f"  {key}: {len(value['rows'])}条")
        else:
            print(f"  {key}: {value}")
