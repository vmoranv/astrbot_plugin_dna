import asyncio
import json
from pathlib import Path
from datetime import datetime, timedelta
import aiohttp

from astrbot.api import logger
from astrbot.api.event import filter, AstrMessageEvent, MessageChain
from astrbot.api.star import Context, Star, StarTools
import astrbot.api.message_components as Comp

# API URL and Headers
API_URL = "https://wiki.ldmnq.com/v1/dna/instanceInfo"
HEADERS = {"game-alias": "dna"}

class DnaInfoPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self.subscriptions = set()
        self.scheduler_task = None
        
        data_dir = StarTools.get_data_dir("dna_info")
        data_dir.mkdir(parents=True, exist_ok=True)
        self.subs_file = data_dir / "subscriptions.json"
        
        self._load_subscriptions()

    def _load_subscriptions(self):
        """Load subscriptions from file."""
        try:
            if self.subs_file.exists():
                with open(self.subs_file, "r", encoding="utf-8") as f:
                    self.subscriptions = set(json.load(f))
                logger.info(f"成功加载 {len(self.subscriptions)} 个订阅。")
            else:
                logger.info("订阅文件不存在，将在首次订阅时创建。")
        except Exception as e:
            logger.error(f"加载订阅失败: {e}")

    def _save_subscriptions(self):
        """Save subscriptions to file."""
        try:
            with open(self.subs_file, "w", encoding="utf-8") as f:
                json.dump(list(self.subscriptions), f, ensure_ascii=False, indent=4)
        except Exception as e:
            logger.error(f"保存订阅失败: {e}")

    async def initialize(self):
        """Start the background scheduler task."""
        self.scheduler_task = asyncio.create_task(self.scheduler())
        logger.info("DNA信息播报插件已启动定时任务。")

    async def terminate(self):
        """Stop the background scheduler task."""
        if self.scheduler_task:
            self.scheduler_task.cancel()
        self._save_subscriptions()
        logger.info("DNA信息播报插件已停止。")

    @filter.command("dna_sub", alias={"dna订阅"})
    async def handle_subscription(self, event: AstrMessageEvent):
        """订阅或取消订阅DNA信息播报。"""
        umo = event.unified_msg_origin
        if umo in self.subscriptions:
            self.subscriptions.remove(umo)
            self._save_subscriptions()
            yield event.plain_result("已取消订阅每小时二重螺旋信息播报。")
        else:
            self.subscriptions.add(umo)
            self._save_subscriptions()
            yield event.plain_result("已成功订阅每小时二重螺旋信息播报！将在下一个整点为您播报。")

    @filter.command("测试密函状态")
    async def manual_fetch(self, event: AstrMessageEvent):
        """手动触发一次DNA信息查询并发送给当前用户。"""
        logger.info(f"用户 {event.get_sender_name()} 手动触发查询。")
        message_chain = await self.get_dna_info_message()
        if message_chain:
            yield event.chain_result(message_chain)
        else:
            yield event.plain_result("获取DNA信息失败，请检查后台日志。")

    async def scheduler(self):
        """Runs every hour to fetch and broadcast data."""
        while True:
            now = datetime.now()
            # 为减轻上游压力，延迟一分钟进行查询
            if now.minute < 1:
                next_run_time = now.replace(minute=1, second=0, microsecond=0)
            else:
                next_run_time = (now + timedelta(hours=1)).replace(minute=1, second=0, microsecond=0)
            
            wait_seconds = (next_run_time - now).total_seconds()
            logger.info(f"下一次播报在 {next_run_time}, 等待 {wait_seconds:.2f} 秒。")
            await asyncio.sleep(wait_seconds)
            
            await self.fetch_and_broadcast()
            
            # Additional small sleep to prevent multiple runs in the same second
            await asyncio.sleep(1)

    async def get_dna_info_message(self):
        """获取并格式化DNA信息，返回消息链。"""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(API_URL, headers=HEADERS) as response:
                    if response.status != 200:
                        logger.error(f"请求API失败，状态码: {response.status}")
                        return None
                    data = await response.json()

            if data.get("code") != 0:
                logger.error(f"API返回错误: {data.get('msg')}")
                return None

            instances_data = data.get("data", [])
            if len(instances_data) < 3:
                logger.error("API返回的数据格式不正确，缺少instances。")
                return None

            role = "、".join([inst["name"] for inst in instances_data[0].get("instances", [])])
            weapon = "、".join([inst["name"] for inst in instances_data[1].get("instances", [])])
            wedge = "、".join([inst["name"] for inst in instances_data[2].get("instances", [])])

            message_text = f"当前DNA信息：\n角  色：{role}\n武  器：{weapon}\n魔之楔：{wedge}"
            return [Comp.Plain(message_text)]

        except aiohttp.ClientError as e:
            logger.error(f"网络请求错误: {e}")
        except json.JSONDecodeError as e:
            logger.error(f"JSON解析错误: {e}")
        except Exception as e:
            logger.error(f"处理DNA信息时发生未知错误: {e}", exc_info=True)
        return None

    async def fetch_and_broadcast(self):
        """Fetches data from the API and broadcasts it to subscribers."""
        logger.info("开始获取DNA信息...")
        
        message_components = await self.get_dna_info_message()
        if not message_components:
            logger.warning("获取DNA信息失败，本轮不播报。")
            return

        message_to_send = MessageChain(message_components)
        logger.info(f"获取到DNA信息，准备向 {len(self.subscriptions)} 个订阅者发送。")
        
        if not self.subscriptions:
            logger.info("没有订阅者，取消发送。")
            return

        for umo in self.subscriptions:
            try:
                await self.context.send_message(umo, message_to_send)
                logger.debug(f"成功发送到 {umo}")
            except Exception as e:
                logger.error(f"发送到 {umo} 失败: {e}")
        
        logger.info("本轮播报完成。")
