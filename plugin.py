# src/plugins/hai_turtle_soup/plugin.py
import os
import json
import aiohttp
from typing import List, Tuple, Type, Optional
from src.plugin_system import (
    BasePlugin,
    register_plugin,
    BaseCommand,
    ComponentInfo,
    ConfigField
)

PLUGIN_DIR = os.path.dirname(__file__)

# 全局游戏状态存储
game_states = {}  # {group_id: {"current_question": "", "current_answer": "", "hints_used": 0, "game_active": False, "guess_history": [], "game_over": False}}

@register_plugin
class HaiTurtleSoupPlugin(BasePlugin):
    plugin_name = "turtlesoup_plugin"
    plugin_description = "支持全程 LLM 的海龟汤游戏插件"
    plugin_version = "1.6.0"
    plugin_author = "Unreal and 何夕"
    enable_plugin = True

    dependencies = []
    python_dependencies = ["aiohttp"]

    config_file_name = "config.toml"
    config_section_descriptions = {
        "plugin": "插件启用配置",
        "llm": "LLM API 配置"
    }

    config_schema = {
        "plugin": {
            "enabled": ConfigField(
                type=bool,
                default=True,
                description="是否启用海龟汤插件"
            ),
            "config_version": ConfigField(
                type=str,
                default="1.6.0",
                description="配置文件版本"
            ),
        },
        "llm": {
            "api_url": ConfigField(
                type=str,
                default="https://api.siliconflow.cn/v1/chat/completions",
                description="LLM API 地址"
            ),
            "api_key": ConfigField(
                type=str,
                default="YOUR_KEY",
                description="LLM API 密钥"
            ),
            "model": ConfigField(
                type=str,
                default="gpt-3.5-turbo",
                description="使用的模型"
            ),
            "temperature": ConfigField(
                type=float,
                default=0.7,
                description="文本生成随机性"
            )
        }
    }

    def get_plugin_components(self) -> List[Tuple[ComponentInfo, Type]]:
        return [
            (HaiTurtleSoupCommand.get_command_info(), HaiTurtleSoupCommand),
        ]

class HaiTurtleSoupCommand(BaseCommand):
    command_name = "HaiTurtleSoupCommand"
    command_description = "生成海龟汤题目或互动 /hgt [问题|提示|整理线索|汤面|猜谜|退出|帮助|揭秘]"
    command_pattern = r"^/hgt(?:\s+(?P<action>(?:提示|问题|整理线索|猜谜|退出|帮助|揭秘|汤面)))(?:\s+(?P<rest>.+))?$"
    command_help = (
    "海龟汤游戏:\n"
    "/hgt 问题(生成题目)\n"
    "/hgt 问题 这里加上你的问题 (向bot提问)\n"
    "/hgt 提示 (获取提示)\n"
    "/hgt 整理线索 (整理线索)\n"
    "/hgt 汤面 (查看当前题目)\n"   # 👈 新增
    "/hgt 猜谜 <答案> (猜测汤底)\n"
    "/hgt 退出 (结束游戏)\n"
    "/hgt 揭秘 (直接查看答案并结束游戏)\n"
    "/hgt 帮助 (查看帮助)"
    )

    command_examples = [
        "/hgt 问题",
        "/hgt 问题 为什么海龟不喝水？",
        "/hgt 提示",
        "/hgt 整理线索",
        "/hgt 汤面", 
        "/hgt 猜谜 海龟是用海龟做的",
        "/hgt 退出",
        "/hgt 揭秘",
        "/hgt 帮助"
    ]
    intercept_message = True

    async def execute(self):
        matched_groups = self.matched_groups or {}
        action = str(matched_groups.get("action") or "").strip()
        rest_input = str(matched_groups.get("rest") or "").strip()

        chat_stream = getattr(self, 'chat_stream', None) or getattr(getattr(self, 'message', None), 'chat_stream', None)
        if chat_stream is None:
            await self.send_text("❌ 无法获取聊天上下文信息")
            return False, "缺少chat_stream", True
        stream_id = getattr(chat_stream, 'stream_id', None)

        # 检查插件是否启用
        if not self.get_config("plugin.enabled", True):
            await self.send_text("❌ 插件已被禁用")
            return False, "插件未启用", True

        # 获取 LLM 配置
        api_url = self.get_config("llm.api_url", "")
        api_key = self.get_config("llm.api_key", "")
        model = self.get_config("llm.model", "gpt-3.5-turbo")
        temperature = self.get_config("llm.temperature", 0.7)

        if not api_url or not api_key:
            await self.send_text("❌ LLM API 配置不完整")
            return False, "API配置错误", True

        # 获取群/用户 ID
        group_id = getattr(chat_stream, 'group_info', None)
        if group_id:
            group_id = group_id.group_id
        else:
            group_id = getattr(getattr(chat_stream, 'user_info', None), 'user_id', "unknown")

        # 初始化游戏状态
        game_state = game_states.get(group_id, {})
        if group_id not in game_states:
            game_states[group_id] = game_state

        # --- 分支逻辑 ---
        if action == "问题" and rest_input:
            return await self._handle_question(group_id, rest_input, api_url, api_key, model, temperature)
        elif action == "提示":
            return await self._handle_hint(group_id, api_url, api_key, model, temperature)
        elif action == "整理线索":
            return await self._handle_clues(group_id, api_url, api_key, model, temperature)
        elif action == "汤面":
            state = game_states.get(group_id)
            if not state.get("game_active"):
                await self.send_text("❌ 当前没有进行中的游戏")
                return False, "无游戏", True
            await self.send_text(f"🍲 当前海龟汤题目:\n{state.get('current_question')}")
            return True, "查看汤面", True
        elif action == "猜谜" and rest_input:
            return await self._handle_guess(group_id, rest_input, api_url, api_key, model, temperature)
        elif action == "揭秘":
            # 用户请求直接查看答案
            if not game_state.get("game_active", False):
                await self.send_text("❌ 当前没有正在进行的游戏。请先使用 /hgt 生成题目。")
                return False, "无游戏", True

            answer = game_state.get("current_answer", "无答案")
            await self.send_text(f"🔓 当前海龟汤答案是:\n{answer}\n游戏结束。")
            
            # 标记游戏结束
            game_state["game_active"] = False
            game_state["game_over"] = True
            game_states[group_id] = game_state  # 保存更新后的状态

            return True, "已揭秘", True
        elif action == "退出":
            return await self._handle_exit(group_id)
        elif action == "帮助":
            await self.send_text(self.command_help)
            return True, "显示帮助", True
        else:
            return await self._start_new_game(group_id, api_url, api_key, model, temperature, stream_id)

    # --- 游戏逻辑方法 ---
    async def _handle_question(self, group_id, question, api_url, api_key, model, temperature):
        state = game_states.get(group_id)
        if not state.get("game_active"):
            return await self._start_new_game(group_id, api_url, api_key, model, temperature, None)

        state.setdefault("guess_history", []).append({"type": "question", "content": question})

        prompt = f"""
你是一个海龟汤游戏专家。
当前题目: {state.get('current_question')}
当前答案: {state.get('current_answer')}
用户提问: {question}
请用简短的回答回应玩家，不要透露答案。
"""
        llm_response = await self._call_llm_api(prompt, api_url, api_key, model, temperature)
        reply = llm_response.strip() or "❌ LLM未返回回答"
        await self.send_text(f"❓ 你问: {question}\n💡 回答: {reply}")
        return True, "问题回答完成", True

    async def _handle_hint(self, group_id, api_url, api_key, model, temperature):
        state = game_states.get(group_id)
        if not state.get("game_active"):
            await self.send_text("❌ 当前没有进行中的游戏")
            return False, "无游戏", True
        if state.get("hints_used", 0) >= 3:
            await self.send_text("💡 提示已用完")
            return False, "提示用尽", True

        prompt = f"""
你是一个海龟汤游戏专家。
题目: {state.get('current_question')}
答案: {state.get('current_answer')}
请提供一个不直接透露答案的提示。
"""
        hint = await self._call_llm_api(prompt, api_url, api_key, model, temperature)
        state["hints_used"] = state.get("hints_used", 0) + 1
        game_states[group_id] = state
        await self.send_text(f"💡 提示 ({state['hints_used']}/3): {hint.strip()}")
        return True, "提示完成", True

    async def _handle_clues(self, group_id, api_url, api_key, model, temperature):
        state = game_states.get(group_id)
        if not state.get("game_active"):
            await self.send_text("❌ 当前没有进行中的游戏")
            return False, "无游戏", True
        guess_history = "\n".join([item["content"] for item in state.get("guess_history", [])])
        prompt = f"""
你是一个海龟汤游戏专家。
题目: {state.get('current_question')}
答案: {state.get('current_answer')}
请整理关键线索，简明列出，不包含答案。
已有记录:
{guess_history}
"""
        clues = await self._call_llm_api(prompt, api_url, api_key, model, temperature)
        await self.send_text(f"📝 线索整理:\n{clues.strip()}")
        return True, "线索整理完成", True

    async def _handle_guess(self, group_id, guess, api_url, api_key, model, temperature):
        state = game_states.get(group_id)
        if not state.get("game_active"):
            await self.send_text("❌ 当前没有进行中的游戏")
            return False, "无游戏", True
        if state.get("game_over"):
            await self.send_text("❌ 游戏已结束，请开始新游戏")
            return False, "游戏已结束", True

        prompt = f"""
你是海龟汤游戏专家。
题目: {state.get('current_question')}
答案: {state.get('current_answer')}
用户猜测: {guess}
请仅回答 是/不是/无关。
"""
        llm_response = (await self._call_llm_api(prompt, api_url, api_key, model, temperature)).strip().lower()
        state.setdefault("guess_history", []).append(guess)
        game_states[group_id] = state

        if llm_response == "是":
            state["game_over"] = True
            game_states[group_id] = state
            await self.send_text(f"🎉 猜对了！答案: {state.get('current_answer')}")
        elif llm_response == "不是":
            await self.send_text(f"❌ 猜错了！提示次数: {state.get('hints_used',0)}/3")
        else:
            await self.send_text("❓ 你的回答与题目无关")
        return True, "猜谜完成", True

    async def _handle_exit(self, group_id):
        game_states[group_id] = {"current_question":"","current_answer":"","hints_used":0,"game_active":False,"guess_history":[],"game_over":False}
        await self.send_text("🛑 游戏已退出")
        return True, "退出游戏", True

    async def _start_new_game(self, group_id, api_url, api_key, model, temperature, stream_id):
        state = game_states.get(group_id, {})

    # 如果已经有题目在进行中，就不允许再出题
        if state.get("game_active", False) and not state.get("game_over", False):
            await self.send_text("⚠️ 当前已经有题目在进行中，请先使用 /hgt 揭秘 或 /hgt 退出 再开始新题。")
            return False, "已有进行中的游戏", True

        # 生成题目
        prompt_question = """
你是一个专业的海龟汤故事生成器。
请生成一个新颖的海龟汤题目，不要使用经典题材（比如：餐馆、飞机、婚礼、海龟等）。
要求：
1. 题目简短，最好 25~50 字。
2. 题目必须是海龟汤风格的推理谜题，包含一个看似矛盾或奇怪的情境。
3. 不要在题目里包含答案。
4. 可以包含猎奇和恐怖元素(如杀人之类的)。
5. 题目结尾应该留有悬念，让人好奇真相。
6. 生成的题目应该是原创的，不要复制已有例子。

请生成一个海龟汤题目。

可以参考的海龟汤汤面and汤底（仅供参考，可以套模版或者直接搬，但是严格按照输出格式，仅输出汤面）：
1.【子的爱】
汤面：我的父母都不理我，但我还是很爱他们。
汤底：小时候我是个很听话的孩子，爸爸妈妈经常给我好吃的水果，我吃不完。他们就告诉我喜欢的东西一定要放进冰箱，这样可以保鲜，记得那时候他们工作可辛苦了，经常加班到深夜。没睡过一个好觉。于是我耍了个小聪明，在他们的水里下了安眠药。他们睡得可香了，然后我把他们放进冰箱里，从那以后我每天都会对他们说：爸爸妈妈我爱你们。现在我都六十了，他们还是那么年轻。

2.【舞】
汤面：我六岁那年，外公去世，我和亲人一起去祭奠，和姐姐玩捉迷藏，然后我对母亲说了句话把她吓昏了过去。
汤底：我去参加外公的葬礼，同行的还有比我大两岁的姐姐，我和她完捉迷藏我没有找到她没想到她躲在了纸做的房子里，当纸房子被点燃，我看见姐姐在跳舞，我对妈说，妈姐姐在那房子里面跳舞，因为姐姐被烧死了，我一直记得这个事。

3.【插进来】
汤面：他迅速的插进来，又迅速的拔出去。反反复复，我流血了。他满头大汗，露出了笑容。“啊，好舒服”
汤底：他是护士，在给我打针，针头打进血管里面会回血，因此说明成功了。流汗是因为反反复复了好几次。

4.【无罪】
汤面："她是自愿的！"尸体无暴力痕迹，凶手被判无罪。"我是无罪的！"尸体有暴力痕迹，凶手也被判无罪。
汤底：第一幕：女儿为救他人（如器官移植）自愿牺牲，所以"自愿"且无暴力痕迹，他人无罪。第二幕：父亲无法接受女儿死亡真相，杀害了被判无罪的人，但法医发现此人所受暴力伤害与父亲行为不符（或父亲伪造证据），真相是女儿死于意外，父亲为报复误杀他人，故父亲也称自己"无罪"，但法律上仍有罪。
"""
        question = (await self._call_llm_api(prompt_question, api_url, api_key, model, temperature)).strip()
        # 生成答案
        prompt_answer = f"""
题目: {question}
你是一个专业的海龟汤故事专家。请生成对应的合理的答案，可以蕴含恐怖元素（比如杀人之类的），讲究逻辑和一些现实，不要解释。150字以内。

可以参考的海龟汤汤面and汤底（仅供参考，可以套模版或者直接搬，但是严格按照输出格式，仅输出汤底）：
1.【子的爱】
汤面：我的父母都不理我，但我还是很爱他们。
汤底：小时候我是个很听话的孩子，爸爸妈妈经常给我好吃的水果，我吃不完。他们就告诉我喜欢的东西一定要放进冰箱，这样可以保鲜，记得那时候他们工作可辛苦了，经常加班到深夜。没睡过一个好觉。于是我耍了个小聪明，在他们的水里下了安眠药。他们睡得可香了，然后我把他们放进冰箱里，从那以后我每天都会对他们说：爸爸妈妈我爱你们。现在我都六十了，他们还是那么年轻。

2.【舞】
汤面：我六岁那年，外公去世，我和亲人一起去祭奠，和姐姐玩捉迷藏，然后我对母亲说了句话把她吓昏了过去。
汤底：我去参加外公的葬礼，同行的还有比我大两岁的姐姐，我和她完捉迷藏我没有找到她没想到她躲在了纸做的房子里，当纸房子被点燃，我看见姐姐在跳舞，我对妈说，妈姐姐在那房子里面跳舞，因为姐姐被烧死了，我一直记得这个事。

3.【插进来】
汤面：他迅速的插进来，又迅速的拔出去。反反复复，我流血了。他满头大汗，露出了笑容。"啊，好舒服"
汤底：他是实习护士，在给我打针，针头打进血管里面会回血，因此说明成功了。流汗是因为反反复复了好几次，让人紧张。

4.【无罪】
汤面："她是自愿的！"尸体无暴力痕迹，凶手被判无罪。"我是无罪的！"尸体有暴力痕迹，凶手也被判无罪。
汤底：第一幕：女儿为救他人（如器官移植）自愿牺牲，所以"自愿"且无暴力痕迹，他人无罪。第二幕：父亲无法接受女儿死亡真相，杀害了被判无罪的人，但法医发现此人所受暴力伤害与父亲行为不符（或父亲伪造证据），真相是女儿死于意外，父亲为报复误杀他人，故父亲也称自己"无罪"，但法律上仍有罪。
"""
        answer = (await self._call_llm_api(prompt_answer, api_url, api_key, model, temperature)).strip()
        game_states[group_id] = {"current_question": question, "current_answer": answer, "hints_used":0, "game_active":True, "guess_history":[], "game_over":False}
        await self.send_text(f"🤔 海龟汤题目:\n{question}\n💡 提示次数: 0/3\n💡 使用 /hgt 问题 <问题> 提问，/hgt 提示 获取提示，/hgt 猜谜 <答案> 猜测汤底")
        return True, "新题目生成完成", True

    async def _call_llm_api(self, prompt, api_url, api_key, model, temperature):
        headers = {"Content-Type": "application/json","Authorization": f"Bearer {api_key}"}
        payload = {"model": model,"messages":[{"role":"system","content":"你是一个专业海龟汤故事生成器和解释者。"},{"role":"user","content":prompt}],"temperature":temperature,"max_tokens":500,"stream":False}
        try:
            timeout = aiohttp.ClientTimeout(total=30)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(api_url, headers=headers, json=payload) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return data.get("choices",[{}])[0].get("message",{}).get("content","").strip()
                    else:
                        return ""
        except Exception as e:
            print(f"LLM API异常: {e}")
            return ""
