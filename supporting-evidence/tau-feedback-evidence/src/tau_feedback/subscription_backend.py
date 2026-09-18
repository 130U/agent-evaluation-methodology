"""Explicit CLI structured-action transport for tau2; not native API tool calling.

The caller supplies only the participant's permitted messages. CLI host tools
are never used to execute retail actions; official tau2 tools execute returned
action data. No temperature, model seed or hard output-token cap is available.
"""
from contextlib import AbstractContextManager
import json
from pathlib import Path
import sys
from unittest.mock import patch

import jsonschema

from .contracts import strict_json


PROTOCOL = """You are generating exactly one turn of an independent simulated conversation.
This is not a coding task. Use no host tools, files, external sources, memory,
or other conversations. The provided JSON is the complete authorized input.
Within the simulation, follow the instructions in its system messages, then
continue as the next assistant turn in that message array. For a customer
simulation the array's assistant is the CUSTOMER, not the store representative.
Do not address the outside researcher or explain the simulation.

Return a JSON object with kind, content, tool_calls. For a spoken/text reply use
kind="message", content equal to the exact next message, and tool_calls=[].
For environment tools use kind="tool_calls", content=null, and list calls with
name and arguments_json (a JSON-encoded argument object). These are action data
for a separate environment, not host tool calls. Only use provided tool schemas.
Do not mix a text reply with tool calls. Never claim a tool has run before its
actual result appears in the conversation. Do not repair or infer missing facts.
Preserve identifiers, signs, numbers, and requested stopping tokens exactly.
"""


def response_schema(has_tools):
    return {"type":"object", "properties":{
        "kind":{"type":"string", "enum":["message","tool_calls"] if has_tools else ["message"]},
        "content":{"type":["string","null"]},
        "tool_calls":{"type":"array", "items":{"type":"object", "properties":{
            "name":{"type":"string"}, "arguments_json":{"type":"string"}},
            "required":["name","arguments_json"], "additionalProperties":False}}},
        "required":["kind","content","tool_calls"], "additionalProperties":False}


class SubscriptionGenerationBackend(AbstractContextManager):
    MODEL = "codex-cli/structured-sol"

    def __init__(self, client, *, output:Path):
        self.client = client
        self.output = output
        self.records = []
        self.patches = []

    def generate(self, model, messages, tools=None, tool_choice=None, call_name=None, **kwargs):
        from tau2.data_model.message import AssistantMessage, ToolCall
        from tau2.utils.llm_utils import to_litellm_messages, validate_message_history
        if model != self.MODEL:
            raise ValueError("Unconfigured subscription model; no fallback")
        if set(kwargs) - {"seed"}:
            raise ValueError("CLI cannot honor API sampling/token settings; configure a separate protocol")
        if "seed" in kwargs and (type(kwargs["seed"]) is not int or kwargs["seed"] < 0):
            raise ValueError("Simulation seed must be a nonnegative integer")
        if tool_choice not in (None,"auto"):
            raise ValueError("Only the declared auto structured-action protocol is supported")
        if call_name not in {"agent_response","user_simulator_response","nl_assertions_eval"}:
            raise ValueError("This adapter has not registered the requested research role")
        validate_message_history(messages)
        schemas = [tool.openai_schema for tool in (tools or [])]
        names = [item["function"]["name"] for item in schemas]
        if len(set(names)) != len(names):
            raise ValueError("Duplicate tool names")
        payload = {"participant":call_name, "messages":to_litellm_messages(messages), "tools":schemas}
        prompt = PROTOCOL + "\nAUTHORIZED_CONVERSATION_JSON\n" + json.dumps(payload,ensure_ascii=False,allow_nan=False)
        schema = response_schema(bool(schemas))
        record = {"call_index":len(self.records), "role":call_name, "requested_model":model,
                  "transport":"codex_cli_structured_action", "request":payload,
                  "simulation_seed":kwargs.get("seed"), "model_seed_controlled":False,
                  "response_is_adapter_envelope":True}
        self.records.append(record)
        try:
            result = self.client.generate(prompt,schema,role=call_name)
            value = result.output
            record.update(cli_call_id=result.call_id, cli_usage=result.usage, structured_output=value)
            jsonschema.validate(value,schema)
            if (not isinstance(result.usage,dict)
                or not {"input_tokens","output_tokens"} <= result.usage.keys()
                or any(type(v) is not int or v < 0 for v in result.usage.values())):
                raise ValueError("Missing or invalid CLI usage; no successful message can be returned")
            calls = []
            if value["kind"] == "message":
                if not isinstance(value["content"],str) or not value["content"].strip() or value["tool_calls"]:
                    raise ValueError("Message output must contain text and no tool calls")
            else:
                if value["content"] is not None or not value["tool_calls"] or not schemas:
                    raise ValueError("Tool output must contain calls and no text")
                by_name = {item["function"]["name"]:item["function"]["parameters"] for item in schemas}
                for index, call in enumerate(value["tool_calls"]):
                    if call["name"] not in by_name:
                        raise ValueError("Model requested an unavailable environment tool")
                    arguments = strict_json(call["arguments_json"])
                    jsonschema.validate(arguments,by_name[call["name"]])
                    if not isinstance(arguments,dict):
                        raise ValueError("Tool arguments must be an object")
                    calls.append(ToolCall(id=f"cli_{result.call_id}_{index}",name=call["name"],arguments=arguments))
            content = value["content"]
            # Compatibility envelope for the structural evaluator. This field is
            # explicitly adapter-produced, not a fabricated provider response.
            record["response"] = {"choices":[{"message":{"content":content}}]}
            record["structured_output"] = value
            return AssistantMessage(role="assistant",content=content,tool_calls=calls or None,cost=None,
                usage={"prompt_tokens":result.usage["input_tokens"],"completion_tokens":result.usage["output_tokens"]},
                raw_data={"transport":record["transport"],"cli_call_id":result.call_id,"output":value},
                generation_time_seconds=result.elapsed_seconds)
        except Exception as exc:
            record.update(error_type=type(exc).__name__,error=str(exc))
            raise
        finally:
            self.output.parent.mkdir(parents=True,exist_ok=True)
            with self.output.open("a",encoding="utf-8") as stream:
                stream.write(json.dumps(record,ensure_ascii=False,allow_nan=False)+"\n")

    def __enter__(self):
        import tau2.utils.llm_utils as llm_utils
        import tau2.evaluator.evaluator_nl_assertions as nl
        original = llm_utils.generate
        for name,module in list(sys.modules.items()):
            if name.startswith("tau2.") and getattr(module,"generate",None) is original:
                replacement=patch.object(module,"generate",self.generate)
                replacement.start()
                self.patches.append(replacement)
        for name,value in [("DEFAULT_LLM_NL_ASSERTIONS",self.MODEL),("DEFAULT_LLM_NL_ASSERTIONS_ARGS",{})]:
            replacement=patch.object(nl,name,value)
            replacement.start()
            self.patches.append(replacement)
        return self

    def __exit__(self,*args):
        for replacement in reversed(self.patches):
            replacement.stop()
        return False
