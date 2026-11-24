// OpenAI Responses API bridge.
//
// Several relays (Codex-oriented stations such as xingchiapi.com) refuse
// /chat/completions outright and only dispatch /responses. This service keeps
// one internal contract — OpenAI chat completions — so the translation lives
// here, next to the single egress point, instead of forking every caller.
// Wire selection is operator-only and origin-exact (see `modelResponsesWire`).
//
// The bridge is deliberately lossy in one direction only: it forwards the
// fields this service actually sends (messages, tools, tool choice, sampling
// limits, reasoning effort) and rebuilds chat-completions frames from the
// response. Anything it does not understand is dropped rather than guessed.

export type ChatBody = Record<string, any>;

const textOf = (content: any): string =>
  typeof content === 'string'
    ? content
    : Array.isArray(content)
      ? content.map((part) => (typeof part === 'string' ? part : typeof part?.text === 'string' ? part.text : '')).join('')
      : '';

function inputItem(message: any): any[] {
  const role = message?.role;
  if (role === 'assistant') {
    const items: any[] = [];
    const text = textOf(message.content);
    if (text) items.push({ type: 'message', role: 'assistant', content: [{ type: 'output_text', text }] });
    for (const call of Array.isArray(message.tool_calls) ? message.tool_calls : []) {
      const name = call?.function?.name;
      if (typeof name !== 'string' || !name) continue;
      items.push({
        type: 'function_call',
        call_id: typeof call.id === 'string' && call.id ? call.id : name,
        name,
        arguments: typeof call.function?.arguments === 'string' ? call.function.arguments : '{}',
      });
    }
    return items;
  }
  if (role === 'tool') {
    return [{ type: 'function_call_output', call_id: typeof message.tool_call_id === 'string' ? message.tool_call_id : '', output: textOf(message.content) }];
  }
  const parts = Array.isArray(message.content) ? message.content : [{ type: 'text', text: textOf(message.content) }];
  return [{
    type: 'message',
    role: role === 'system' || role === 'developer' ? role : 'user',
    content: parts.map((part: any) => part?.type === 'image_url'
      ? { type: 'input_image', image_url: typeof part.image_url === 'string' ? part.image_url : part.image_url?.url }
      : { type: 'input_text', text: typeof part?.text === 'string' ? part.text : '' }),
  }];
}

export function responsesRequest(body: ChatBody) {
  const messages = Array.isArray(body?.messages) ? body.messages : [];
  const request: Record<string, any> = {
    model: typeof body?.model === 'string' ? body.model : '',
    input: messages.flatMap(inputItem),
  };
  if (body?.stream === true) request.stream = true;
  const limit = body?.max_completion_tokens ?? body?.max_tokens;
  if (Number.isInteger(limit) && limit > 0) request.max_output_tokens = limit;
  for (const key of ['temperature', 'top_p'] as const) if (typeof body?.[key] === 'number') request[key] = body[key];
  const tools = (Array.isArray(body?.tools) ? body.tools : []).filter((tool: any) => typeof tool?.function?.name === 'string' && tool.function.name);
  if (tools.length) {
    request.tools = tools.map((tool: any) => ({
      type: 'function',
      name: tool.function.name,
      ...(typeof tool.function.description === 'string' && tool.function.description ? { description: tool.function.description } : {}),
      parameters: tool.function.parameters && typeof tool.function.parameters === 'object' ? tool.function.parameters : { type: 'object', properties: {} },
      strict: tool.function.strict === true,
    }));
  }
  const choice = body?.tool_choice;
  if (typeof choice === 'string' && ['auto', 'none', 'required'].includes(choice)) request.tool_choice = choice;
  else if (typeof choice?.function?.name === 'string' && choice.function.name) request.tool_choice = { type: 'function', name: choice.function.name };
  if (typeof body?.parallel_tool_calls === 'boolean') request.parallel_tool_calls = body.parallel_tool_calls;
  if (typeof body?.reasoning_effort === 'string' && body.reasoning_effort) request.reasoning = { effort: body.reasoning_effort };
  return request;
}

function usageOf(json: any) {
  const usage = json?.usage;
  if (!usage) return undefined;
  const prompt = typeof usage.input_tokens === 'number' ? usage.input_tokens : typeof usage.prompt_tokens === 'number' ? usage.prompt_tokens : undefined;
  const completion = typeof usage.output_tokens === 'number' ? usage.output_tokens : typeof usage.completion_tokens === 'number' ? usage.completion_tokens : undefined;
  if (prompt === undefined && completion === undefined) return undefined;
  const cached = usage.input_tokens_details?.cached_tokens ?? usage.prompt_tokens_details?.cached_tokens;
  return {
    prompt_tokens: prompt ?? 0,
    completion_tokens: completion ?? 0,
    total_tokens: typeof usage.total_tokens === 'number' ? usage.total_tokens : (prompt ?? 0) + (completion ?? 0),
    ...(typeof cached === 'number' ? { prompt_tokens_details: { cached_tokens: cached } } : {}),
  };
}

export function chatCompletionFromResponses(json: any, fallbackModel: string) {
  if (!['completed', 'incomplete'].includes(json?.status)) throw new Error('upstream_responses_not_complete');
  let text = '';
  const calls: any[] = [];
  for (const item of Array.isArray(json?.output) ? json.output : []) {
    if (item?.type === 'message') for (const part of Array.isArray(item.content) ? item.content : []) if (part?.type === 'output_text' && typeof part.text === 'string') text += part.text;
    if (item?.type === 'function_call') {
      calls.push({
        id: typeof item.call_id === 'string' && item.call_id ? item.call_id : String(item.id ?? ''),
        type: 'function',
        function: { name: typeof item.name === 'string' ? item.name : '', arguments: typeof item.arguments === 'string' ? item.arguments : '{}' },
      });
    }
  }
  const message: any = { role: 'assistant', content: text || null };
  if (calls.length) message.tool_calls = calls.map((call, index) => ({ ...call, index }));
  const usage = usageOf(json);
  return {
    id: typeof json?.id === 'string' && json.id ? json.id : 'resp_bridge',
    object: 'chat.completion',
    created: typeof json?.created_at === 'number' ? json.created_at : Math.floor(Date.now() / 1000),
    model: typeof json?.model === 'string' && json.model ? json.model : fallbackModel,
    choices: [{ index: 0, message, finish_reason: json.status === 'incomplete' ? 'length' : calls.length ? 'tool_calls' : 'stop' }],
    ...(usage ? { usage } : {}),
  };
}

// Chat-completions SSE rebuilt from Responses events. Unknown events are
// ignored; a terminal failure is surfaced as a chat error frame so callers
// keep their existing error path instead of reading a truncated success.
export function responsesStreamToChat(model: string) {
  const encoder = new TextEncoder(), decoder = new TextDecoder();
  const created = Math.floor(Date.now() / 1000);
  let buffer = '', roleSent = false, terminal = false, finish = 'stop', usage: ReturnType<typeof usageOf>;
  const calls = new Set<number>();
  const frame = (delta: any, choice: any = {}, root: any = {}) => 'data: ' + JSON.stringify({ id: 'chatcmpl-bridge', object: 'chat.completion.chunk', created, model, choices: [{ index: 0, delta, ...choice }], ...root }) + '\n\n';
  return new TransformStream<Uint8Array, Uint8Array>({
    transform(bytes, controller) {
      buffer += decoder.decode(bytes, { stream: true });
      let split: number;
      while ((split = buffer.indexOf('\n')) >= 0) {
        const line = buffer.slice(0, split).replace(/\r$/, '');
        buffer = buffer.slice(split + 1);
        if (!line.startsWith('data:')) continue;
        const payload = line.slice(5).trim();
        if (!payload || payload === '[DONE]') continue;
        let event: any;
        try { event = JSON.parse(payload); } catch { continue; }
        if (!roleSent) { roleSent = true; controller.enqueue(encoder.encode(frame({ role: 'assistant', content: '' }))); }
        const type = event?.type;
        const index = Number.isInteger(event?.output_index) ? event.output_index : 0;
        if (type === 'response.output_text.delta' && typeof event.delta === 'string') controller.enqueue(encoder.encode(frame({ content: event.delta })));
        else if (type === 'response.output_item.added' && event.item?.type === 'function_call') {
          calls.add(index);
          controller.enqueue(encoder.encode(frame({ tool_calls: [{ index, id: typeof event.item.call_id === 'string' && event.item.call_id ? event.item.call_id : String(event.item.id ?? ''), type: 'function', function: { name: typeof event.item.name === 'string' ? event.item.name : '', arguments: '' } }] })));
        } else if (type === 'response.function_call_arguments.delta' && typeof event.delta === 'string') {
          controller.enqueue(encoder.encode(frame({ tool_calls: [{ index, function: { arguments: event.delta } }] })));
        } else if (type === 'response.completed' || type === 'response.incomplete') {
          if (terminal || event.response?.status !== (type === 'response.completed' ? 'completed' : 'incomplete')) throw new Error('upstream_responses_invalid_terminal');
          terminal = true;
          usage = usageOf(event.response) ?? usage;
          finish = type === 'response.incomplete' ? 'length' : calls.size ? 'tool_calls' : 'stop';
        } else if (type === 'response.failed' || type === 'error') {
          throw new Error('upstream_responses_failed');
        }
      }
    },
    flush(controller) {
      if (!terminal) throw new Error('upstream_responses_missing_terminal');
      controller.enqueue(encoder.encode(frame({}, { finish_reason: finish }, usage ? { usage } : {})));
      controller.enqueue(encoder.encode('data: [DONE]\n\n'));
    },
  });
}
