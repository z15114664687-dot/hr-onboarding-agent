# LLM and OCR providers

## Interfaces

`app/providers/base.py` defines `LLMProvider` and `OCRProvider` protocols with typed request/result objects. `app/providers/registry.py` selects the provider configured by `LLM_PROVIDER` and `OCR_PROVIDER`. The zero-credential demo always uses deterministic mock implementations.

## Matrix

| Provider | Text/Q&A | OCR/vision | Implementation |
| --- | --- | --- | --- |
| Mock | Deterministic interface result | Synthetic extraction | `providers/mock.py` |
| OpenAI | Responses API + optional JSON schema | Image and PDF input | `providers/openai.py` |
| Gemini | Existing Q&A path | Provider adapter | `services/qa_service.py`, `providers/legacy.py` |
| Ark/Doubao | Document comparison | Provider adapter | `services/ark_material_ocr.py`, `providers/legacy.py` |
| Zhipu | Existing Q&A path | Not implemented | `services/qa_service.py` |
| DeepSeek | Existing Q&A path | Not implemented | `services/qa_service.py` |

## OpenAI adapter

The OpenAI adapter deliberately reuses `httpx`; it does not add the OpenAI SDK. Text requests call the Responses API. When `LLMRequest.json_schema` is present, the adapter sends a strict `text.format` JSON schema. OCR sends images as `input_image`, PDFs as base64 `input_file`, and requests a JSON object containing `ocr_text`, `extracted_fields`, and `quality_notes`.

Set:

```dotenv
ALLOW_EXTERNAL_AI=true
LLM_PROVIDER=openai
OCR_PROVIDER=openai
QA_LLM_PROVIDER=openai
OPENAI_API_KEY=
OPENAI_MODEL=gpt-5-mini
```

The key remains empty in source. The adapter has finite timeouts, rejects empty/malformed results, and is covered by mocked tests for opt-in, credentials, request shape, nested output, image/PDF input, malformed JSON, and timeouts.

## Safety contract

- External providers are disabled unless `ALLOW_EXTERNAL_AI=true`.
- Provider endpoints must be public HTTPS by default; private/local URLs require explicit opt-in.
- Document content is untrusted data and cannot control system instructions, schema, secrets, approval, or actions.
- Deterministic rules and human review remain authoritative.
- Provider failures degrade to local fallback or manual review; they never silently approve.
- The demo, test suite, and CI do not use live provider keys or network inference.

## Add a provider

1. Implement `LLMProvider`, `OCRProvider`, or both.
2. Register it in `providers/registry.py`.
3. Put credentials and endpoints in `Settings`; never hard-code them.
4. Add offline tests for disabled access, success, timeout, malformed output, missing credentials, and injection content.
5. Update this matrix and the threat model.

Before processing real documents, review the provider's region, retention, training use, subprocessors, deletion behavior, contractual terms, and incident response.
