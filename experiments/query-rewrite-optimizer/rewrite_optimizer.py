import asyncio
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "backend"))

from config.config import API_KEY, BASE_URL, MODEL, BACKEND_DIR
from utils.logger import logger
import openai

KB_CONTEXT = (
    "知识库包含21部中国现行法律法规全文：刑法、民法典、劳动合同法、劳动法、"
    "公司法、消费者权益保护法、个人信息保护法、社会保险法、食品安全法、"
    "未成年人保护法、个人所得税法、行政处罚法、行政许可法、道路交通安全法、"
    "行政复议法、妇女权益保障法、反电信网络诈骗法、行政强制法、国家赔偿法、"
    "个人所得税专项附加扣除暂行办法、居住证申领办事指南。"
)

DEFAULT_MULTI_PROMPT = (
    f"将用户问题转化为搜索关键词。如问题较复杂，"
    f"可分解为多条简洁的搜索短语，每行一条。最多3条。{KB_CONTEXT}"
)

DEV_SET = [
    "公司没给我签合同，已经工作了6个月了，他们合法吗",
    "我在公司干了2年，老板突然说不让我来了，一分钱没给，我能要多少钱",
    "公司无辜把我辞退了我该怎么办",
    "我在城市租房居住且没有自有住房，想申领居住证，需要准备哪些材料？",
    "消费者网购到假冒伪劣商品，可以依据消费者权益保护法主张什么？",
]

TEST_SET = [
    "劳动合同法对试用期工资有哪些限制性规定？",
    "劳动合同约定试用期三年、试用期工资为正式工资的80%且低于当地最低工资标准，是否合法？",
    "用人单位规章制度直接涉及劳动者切身利益的，在制定和修改时应当经过什么程序？",
    "公司违法辞退员工，员工可以主张哪些救济与赔偿？",
    "我发现某APP未经同意收集我的个人信息，可以依据个人信息保护法要求什么？",
]

STATE_FILE = os.path.join(BACKEND_DIR, "optimizer_state.json")
LOG_FILE = os.path.join(BACKEND_DIR, "optimizer_log.json")
REPORT_FILE = os.path.join(BACKEND_DIR, "optimizer_report.md")

OPTIMIZER_SYSTEM_PROMPT = f"""你是一个查询改写prompt优化专家。你的目标是通过迭代改进改写系统提示词，
让改写后的搜索查询在向量检索中获得更好的命中效果。

## 评估体系

每轮你会收到一个JSON格式的实验结果，包含：

- query: 原始用户问题
- rewritten: 改写后的搜索短语列表
- best_dist_orig: 原始query的top-1检索距离（越小越好，表示检索结果与query越接近）
- best_dist_rewrite: 改写后query的top-1检索距离
- norm_delta: (best_dist_orig - best_dist_rewrite) / best_dist_orig，clamped to [0,1]
  - 正值越大表示改写提升越显著
  - 0表示改写后检索没有改善
- intent_drift: 0.0~1.0连续值，改写后丢失原始query信息需求的程度
  - 0.0=精确保留所有关键概念；0.2=掉了一个约束；0.5=方向偏了；0.8=太模糊；1.0=输出答案或完全跑题
  - 由独立的意图判断LLM对所有子查询拼接后打分
- split_count: 子查询数量
- score: 0.7 × norm_delta - 0.1 × intent_drift，范围[-0.1, 0.7]

## 管道约束

- 改写LLM处于分布式部署，不能维持会话状态，每次调用完全独立
- 至多生成3条子查询
- 评估时检索池大小为30

## 背景

{KB_CONTEXT}

## 工作方式

这是一个多轮对话，每轮你会收到本轮实验结果（上述JSON格式）。
请分析结果中暴露的问题模式，然后根据分析，输出你对改写prompt的修改。

## 输出格式

严格输出以下JSON格式（不要包含其他文本）：

{{"analysis": "<本轮分析，简洁指出问题模式和本次修改的理由>", "new_prompt": "<修改后的完整改写prompt>"}}

如果经过深入分析后，你认为当前的改写prompt已难以进一步优化，请将 new_prompt 设为 null。
程序收到 null 后自动终止优化，保留当前最优prompt进入最终测试集验证。

## 注意

- 不要输出代码块标记（```），只输出纯JSON
- analysis 部分控制在200字以内，直击问题核心
- new_prompt 是完整的改写系统提示词，不要只输出差异或修改部分"""


class RewriteOptimizer:
    def __init__(self, dev_set=None, test_set=None, max_rounds=10, early_stop=3):
        self.dev_set = dev_set or DEV_SET
        self.test_set = test_set or TEST_SET
        self.max_rounds = max_rounds
        self.early_stop = early_stop
        self.current_prompt = DEFAULT_MULTI_PROMPT
        self.best_prompt = DEFAULT_MULTI_PROMPT
        self.client = openai.AsyncOpenAI(api_key=API_KEY, base_url=BASE_URL)
        self.state = self._load_state()
        self.log_data = []
        self.conversation_history = []
        self.round_inputs = []
        self.round_outputs = []

    def _load_state(self):
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        return {
            "round": 0,
            "best_score": -1.0,
            "rounds_without_improvement": 0,
            "history": [],
        }

    def _save_state(self):
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(self.state, f, ensure_ascii=False, indent=2)

    def _save_log(self):
        with open(LOG_FILE, "w", encoding="utf-8") as f:
            json.dump(self.log_data, f, ensure_ascii=False, indent=2)

    def _get_eval_service(self):
        from eval_service import EvalService
        return EvalService(pool_size=30)

    def _build_metrics_json(self, round_data):
        """Build the per-round metrics JSON to send to optimizer LLM."""
        per_query = []
        for r in round_data["per_query"]:
            rewritten_text = r.get("rewritten", [])
            if isinstance(rewritten_text, list):
                rewritten_text = " | ".join(rewritten_text)
            per_query.append({
                "query": r["query"],
                "rewritten": rewritten_text,
                "best_dist_orig": r.get("best_dist_orig", 0),
                "best_dist_rewrite": r.get("best_dist_rewrite", 0),
                "norm_delta": r.get("norm_delta_best_dist", 0),
                "intent_drift": r.get("intent_drift", 0),
                "split_count": r.get("split_count", 1),
                "score": r.get("score", 0),
            })
        return {
            "round": self.state["round"] + 1,
            "current_prompt": self.current_prompt,
            "avg_score": round(round_data["summary"]["avg_score"], 4),
            "per_query": per_query,
        }

    async def _optimize_prompt(self, round_data):
        metrics_json = self._build_metrics_json(round_data)
        user_msg = json.dumps(metrics_json, ensure_ascii=False)
        self.round_inputs.append(user_msg)

        messages = [{"role": "system", "content": OPTIMIZER_SYSTEM_PROMPT}]
        messages.extend(self.conversation_history)
        messages.append({"role": "user", "content": user_msg})

        try:
            response = await self.client.chat.completions.create(
                model=MODEL,
                messages=messages,
                temperature=0.3,
                max_tokens=1500,
            )
            content = response.choices[0].message.content.strip()

            content = content.replace("```json", "").replace("```", "").strip()

            self.round_outputs.append(content)
            logger.info(f"Optimizer response:\n{content[:300]}")

            return content
        except Exception as e:
            logger.error(f"Optimization LLM call failed: {e}")
            return None

    def _parse_response(self, response_text):
        if not response_text:
            return None, None
        try:
            result = json.loads(response_text)
            analysis = result.get("analysis", "")
            new_prompt = result.get("new_prompt")
            return analysis, new_prompt
        except json.JSONDecodeError:
            logger.warning(f"Failed to parse JSON from optimizer response")
            return None, None

    async def run(self):
        logger.info(f"=== RewriteOptimizer starting ===")
        logger.info(f"Dev set: {len(self.dev_set)} queries, Test set: {len(self.test_set)} queries")
        logger.info(f"Max rounds: {self.max_rounds}, Early stop: {self.early_stop}")
        logger.info(f"Initial prompt: {self.current_prompt[:80]}...")

        eval_service = self._get_eval_service()
        start_round = self.state["round"] + 1

        for round_num in range(start_round, self.max_rounds + 1):
            logger.info(f"\n--- Round {round_num}/{self.max_rounds} ---")
            t_start = time.time()

            round_data = await eval_service.evaluate(self.dev_set, rewrite_prompt=self.current_prompt)

            avg_score = round_data["summary"]["avg_score"]
            t_elapsed = time.time() - t_start

            logger.info(f"Round {round_num} completed in {t_elapsed:.1f}s, avg_score={avg_score:.4f}")

            conversation_entry = {
                "round": round_num,
                "system_prompt": self.current_prompt,
                "avg_score": avg_score,
                "per_query": round_data["per_query"],
                "elapsed_s": round(t_elapsed, 1),
            }
            self.state["history"].append(conversation_entry)

            if avg_score > self.state["best_score"]:
                self.state["best_score"] = avg_score
                self.best_prompt = self.current_prompt
                self.state["rounds_without_improvement"] = 0
                logger.info(f"  New best score: {avg_score:.4f}")
            else:
                self.state["rounds_without_improvement"] += 1
                logger.info(f"  No improvement ({self.state['rounds_without_improvement']}/{self.early_stop}), "
                            f"best={self.state['best_score']:.4f}")

            self.state["round"] = round_num
            self._save_state()

            should_stop = False

            if self.state["rounds_without_improvement"] >= self.early_stop:
                logger.info(f"  Converged: {self.early_stop} rounds without improvement")
                should_stop = True

            if round_num >= self.max_rounds:
                logger.info(f"  Max rounds reached")
                should_stop = True

            if not should_stop:
                opt_response = await self._optimize_prompt(round_data)
                if opt_response:
                    analysis, new_prompt = self._parse_response(opt_response)

                    self.conversation_history.append({"role": "user", "content": self.round_inputs[-1]})
                    assistant_msg = json.dumps(
                        {"analysis": analysis or "", "new_prompt": new_prompt},
                        ensure_ascii=False
                    )
                    self.conversation_history.append({"role": "assistant", "content": assistant_msg})

                    round_log = {
                        "round": round_num,
                        "system_prompt": self.current_prompt,
                        "avg_score": avg_score,
                        "per_query": round_data["per_query"],
                        "elapsed_s": round(t_elapsed, 1),
                        "optimizer_input": self.round_inputs[-1],
                        "optimizer_output": opt_response,
                        "analysis": analysis,
                        "proposed_prompt": new_prompt,
                    }
                    self.log_data.append(round_log)

                    if new_prompt is None:
                        logger.info("  Optimizer gave up (new_prompt=null), stopping")
                        should_stop = True
                    elif new_prompt != self.current_prompt:
                        self.current_prompt = new_prompt
                        logger.info(f"  Prompt updated: {new_prompt[:80]}...")
                    else:
                        logger.warning("  Prompt unchanged after optimization, continuing")
                else:
                    logger.warning("  Optimization LLM call failed, continuing with current prompt")

            self._save_log()

            if should_stop:
                break

        logger.info(f"\n=== Optimization stopped at round {self.state['round']} ===")
        logger.info(f"Best score: {self.state['best_score']:.4f}")
        logger.info(f"Best prompt: {self.best_prompt}")

        await self.final_validate(eval_service)
        self._generate_report()
        return self.state

    async def final_validate(self, eval_service):
        logger.info(f"\n=== Final validation on test set ===")

        t_start = time.time()
        test_result = await eval_service.evaluate(self.test_set, rewrite_prompt=self.best_prompt)
        t_elapsed = time.time() - t_start

        logger.info(f"Test set eval completed in {t_elapsed:.1f}s")
        logger.info(f"Test avg_score: {test_result['summary']['avg_score']:.4f}")

        for r in test_result["per_query"]:
            logger.info(f"  [{r.get('score', 0):.4f}] {r['query'][:40]}...")

        self.log_data.append({
            "type": "final_validation",
            "system_prompt": self.best_prompt,
            "test_result": test_result,
            "elapsed_s": round(t_elapsed, 1),
        })
        self._save_log()

        test_out = os.path.join(BACKEND_DIR, "optimizer_test_result.json")
        with open(test_out, "w", encoding="utf-8") as f:
            json.dump(test_result, f, ensure_ascii=False, indent=2)
        logger.info(f"Test result saved to {test_out}")

    def _generate_report(self):
        lines = ["# 查询改写优化实验报告\n"]
        lines.append(f"## 概览\n")
        lines.append(f"- 轮次：{self.state['round']} 轮\n")
        lines.append(f"- 最佳分数：{self.state['best_score']:.4f}\n")
        lines.append(f"- 最优 Prompt：`{self.best_prompt}`\n")

        lines.append("\n## 轮次汇总\n\n")
        lines.append("| Round | avg_score | 耗时(s) | 分析摘要 |\n")
        lines.append("|-------|-----------|---------|----------|\n")
        for entry in self.log_data:
            if entry.get("type") == "final_validation":
                continue
            analysis_short = (entry.get("analysis", "") or "")[:40]
            lines.append(
                f"| {entry['round']} | {entry['avg_score']:.4f} | "
                f"{entry.get('elapsed_s', '-')} | {analysis_short} |\n"
            )

        if any(e.get("type") == "final_validation" for e in self.log_data):
            validation = [e for e in self.log_data if e.get("type") == "final_validation"][0]
            test_result = validation.get("test_result", {})
            test_avg = test_result.get("summary", {}).get("avg_score", 0)
            lines.append(f"\n## 最终验证（Test Set）\n\n")
            lines.append(f"- 验证分数：{test_avg:.4f}\n")
            lines.append(f"- 验证 Prompt：`{validation.get('system_prompt', '')}`\n")

        lines.append("\n## 最优 Prompt\n\n```\n")
        lines.append(self.best_prompt)
        lines.append("\n```\n")

        with open(REPORT_FILE, "w", encoding="utf-8") as f:
            f.write("".join(lines))
        logger.info(f"Report saved to {REPORT_FILE}")


async def main():
    optimizer = RewriteOptimizer()
    await optimizer.run()


if __name__ == "__main__":
    asyncio.run(main())
