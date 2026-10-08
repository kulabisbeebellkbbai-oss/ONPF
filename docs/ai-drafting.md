# AI drafting

The current implementation uses a separate private LiteLLM gateway with OpenAI as the initial provider. Follow [private gateway setup](ai-drafting-setup.md), [AWS update](aws-ai-drafting-update.md), and [deployment integration](ai-deployment-integration.md).

Users explicitly select evidence and authorize each generation, review the unsaved suggestion, apply chosen fields, then save through the ordinary form. Generating and applying do not save program records or grant approval or publication. Provider credentials stay in the gateway. Future Ollama connections use the same application alias after operator acceptance.

The earlier direct-provider configuration and drafting controls have been replaced. Active `ONPF_AI_BASE_URL`, `ONPF_AI_API_KEY`, and `ONPF_AI_API_KEY_FILE` settings must be migrated to the separate gateway; they cannot activate the current flow. The application loads `/etc/onpf/ai-drafting.env` independently of regenerated production settings.
