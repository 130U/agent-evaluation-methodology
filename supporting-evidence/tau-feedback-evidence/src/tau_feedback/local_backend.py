"""Real local inference adapter at tau2's generation boundary.

No synthetic replies or remote fallbacks. Keeps original message conversion,
tools, simulator, environment, and evaluator code; every call is recorded.
"""
from __future__ import annotations
from contextlib import AbstractContextManager
import json
import math
from pathlib import Path
import sys
import time
from unittest.mock import patch

class LocalBudgetExceeded(RuntimeError):
    pass

class LocalGenerationBackend(AbstractContextManager):
    MODEL="openai/tau-local-qwen"

    def __init__(self,runtime,*,output:Path,seed=17,max_calls=100,max_generated_tokens=16000,max_seconds=1800,max_request_seconds=180):
        self.runtime=runtime
        self.output=output
        self.seed=seed
        self.max_calls=max_calls
        self.max_generated_tokens=max_generated_tokens
        self.max_seconds=max_seconds
        self.max_request_seconds=max_request_seconds
        self.generated_tokens=0
        self.charged_generated_tokens=0
        self.usage_incomplete_calls=0
        self.prompt_tokens=0
        self.records=[]
        self.started=time.monotonic()
        self.phase_deadline=None
        self.patches=[]

    def generate(self,model,messages,tools=None,tool_choice=None,call_name=None,**kwargs):
        from tau2.data_model.message import AssistantMessage,ToolCall
        from tau2.utils.llm_utils import to_litellm_messages,validate_message_history
        if model!=self.MODEL:
            raise ValueError(f"Unconfigured model requested; remote fallback prohibited: {model}")
        unsupported=set(kwargs)-{"max_tokens","temperature","seed"}
        if unsupported:
            raise ValueError(f"Unsupported local generation settings: {sorted(unsupported)}")
        validate_message_history(messages)
        max_tokens=kwargs.get("max_tokens",512)
        if type(max_tokens) is not int or max_tokens<=0:
            raise ValueError("max_tokens must be a positive integer")
        temperature=kwargs.get("temperature",0)
        if type(temperature) not in (int,float) or not math.isfinite(temperature) or temperature<0:
            raise ValueError("temperature must be finite and nonnegative")
        deadline=min(self.started+self.max_seconds,self.phase_deadline or float("inf"))
        if (len(self.records)>=self.max_calls or self.charged_generated_tokens+max_tokens>self.max_generated_tokens
            or time.monotonic()>=deadline):
            raise LocalBudgetExceeded("Local generation budget exhausted before next call")
        payload={"model":"tau-local-qwen","messages":to_litellm_messages(messages),"stream":False,
            "temperature":temperature,"seed":kwargs.get("seed",self.seed+len(self.records)),
            "max_tokens":max_tokens}
        if tools:
            payload["tools"]=[tool.openai_schema for tool in tools]
            payload["tool_choice"]=tool_choice or "auto"
        started=time.monotonic()
        record={"call_index":len(self.records),"role":call_name,"requested_model":model,"request":payload}
        self.records.append(record)
        # Reserve before transport: timeout/missing usage must never be free.
        self.charged_generated_tokens+=max_tokens
        record["charged_generated_tokens"]=max_tokens
        record["usage_complete"]=False
        try:
            response=self.runtime.request("/v1/chat/completions",payload,
                timeout=min(self.max_request_seconds,max(0.001,deadline-time.monotonic())))
            record["response"]=response
            usage=response.get("usage") or {}
            completion=usage.get("completion_tokens")
            prompt=usage.get("prompt_tokens")
            if (type(completion) is not int or not 0<=completion<=max_tokens
                or type(prompt) is not int or prompt<0):
                raise RuntimeError("Missing or invalid usage; reserved generation budget retained")
            self.generated_tokens+=completion
            self.prompt_tokens+=prompt
            self.charged_generated_tokens+=completion-max_tokens
            record.update(charged_generated_tokens=completion,usage_complete=True)
            if response.get("model")!="tau-local-qwen":
                raise RuntimeError("Response model differs from pinned local alias")
            choice=response["choices"][0]
            if choice["finish_reason"]=="length":
                raise RuntimeError("Model output reached token cap; retain as truncated, not complete")
            message=choice["message"]
            calls=[ToolCall(id=c["id"],name=c["function"]["name"],
                           arguments=json.loads(c["function"]["arguments"]))
                   for c in message.get("tool_calls") or []]
            return AssistantMessage(role="assistant",content=message.get("content"),tool_calls=calls or None,
                cost=0.0,usage={"completion_tokens":usage.get("completion_tokens"),"prompt_tokens":usage.get("prompt_tokens")},
                raw_data=response,generation_time_seconds=time.monotonic()-started)
        except Exception as exc:
            record.update(error_type=type(exc).__name__,error=str(exc))
            raise
        finally:
            if not record["usage_complete"]:
                self.usage_incomplete_calls+=1
            record["elapsed_seconds"]=time.monotonic()-started
            self.output.parent.mkdir(parents=True,exist_ok=True)
            with self.output.open("a",encoding="utf-8") as stream:
                stream.write(json.dumps(record,ensure_ascii=False)+"\n")

    def __enter__(self):
        import tau2.utils.llm_utils as llm_utils
        import tau2.evaluator.evaluator_nl_assertions as nl
        original=llm_utils.generate
        for name,module in list(sys.modules.items()):
            if name.startswith("tau2.") and getattr(module,"generate",None) is original:
                replacement=patch.object(module,"generate",self.generate)
                replacement.start()
                self.patches.append(replacement)
        settings=[(nl,"DEFAULT_LLM_NL_ASSERTIONS",self.MODEL),
                  (nl,"DEFAULT_LLM_NL_ASSERTIONS_ARGS",{"temperature":0,"max_tokens":1024})]
        for module,name,value in settings:
            replacement=patch.object(module,name,value)
            replacement.start()
            self.patches.append(replacement)
        return self

    def __exit__(self,*args):
        for replacement in reversed(self.patches):
            replacement.stop()
        return False
