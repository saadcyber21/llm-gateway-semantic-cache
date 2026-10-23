import os


def generate(prompt: str, max_tokens: int, requested_model: str | None = None) -> tuple[str, str, str, int, int]:
    providers = [x.strip().lower() for x in os.getenv("PRIMARY_PROVIDER", "openai,anthropic,mock").split(",") if x.strip()]
    errors = []
    for provider in providers:
        try:
            if provider == "openai" and os.getenv("OPENAI_API_KEY"):
                from openai import OpenAI
                model = requested_model or "gpt-4o-mini"
                response = OpenAI(api_key=os.environ["OPENAI_API_KEY"], timeout=15).chat.completions.create(model=model, messages=[{"role": "user", "content": prompt}], max_tokens=max_tokens)
                text = response.choices[0].message.content or ""
                return text, provider, model, response.usage.prompt_tokens, response.usage.completion_tokens
            if provider == "anthropic" and os.getenv("ANTHROPIC_API_KEY"):
                import anthropic
                model = requested_model or "claude-3-5-haiku-latest"
                response = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"], timeout=15).messages.create(model=model, max_tokens=max_tokens, messages=[{"role": "user", "content": prompt}])
                text = "".join(b.text for b in response.content if getattr(b, "type", None) == "text")
                return text, provider, model, response.usage.input_tokens, response.usage.output_tokens
            if provider == "mock":
                model = requested_model or "mock-v1"
                text = f"Mock response: {prompt[:max_tokens * 3]}"
                return text, provider, model, max(1, len(prompt.split())), max(1, len(text.split()))
        except (ValueError, RuntimeError, OSError) as exc:
            errors.append(provider + ":" + type(exc).__name__)
    raise RuntimeError("All configured providers failed: " + ", ".join(errors))


def stream(prompt: str, max_tokens: int, requested_model: str | None = None):
    text, provider, model, input_tokens, output_tokens = generate(prompt, max_tokens, requested_model)
    for word in text.split(): yield {"type": "token", "text": word + " "}
    yield {"type": "done", "provider": provider, "model": model, "input_tokens": input_tokens, "output_tokens": output_tokens}
