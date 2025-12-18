# AI Setup

ZimX can connect to local or remote AI servers depending on how you configure it.

**Control how much AI you want.** Use local models, remote models, or none at all. ZimX never forces an AI subscription mindset.

## Prerequisites
Before configuring ZimX, you need an AI server running. Choose one:

### Option 1: Local Models (Recommended)
**Pros**:
- Complete privacy
- Works offline
- No API costs
- Full control

**Popular options**:

1. **Ollama** (easiest):
   - Download from ollama.ai
   - Install and run: `ollama serve`
   - Pull a model: `ollama pull llama3` or `ollama pull mistral`
   - Default endpoint: `http://localhost:11434`

2. **LM Studio**:
   - Download from lmstudio.ai
   - GUI for model management
   - Built-in server

3. **llamafile**:
   - Single executable file
   - No installation required

### Option 2: Remote Models
**Pros**:
- More powerful models
- Faster inference
- No local GPU required

**Options**:
- OpenAI API (GPT-4, GPT-3.5)
- Anthropic API (Claude)
- Google API (Gemini)
- Azure OpenAI

**Cons**:
- Requires internet
- API costs
- Data sent to provider

## Configuring ZimX

### Step 1: Open Settings
Access AI settings through the application preferences or settings menu.

### Step 2: Set API Endpoint
**For local models** (e.g., Ollama):
```
http://localhost:11434
```

**For remote models**:
- OpenAI: `https://api.openai.com/v1`
- Custom endpoint for other providers

### Step 3: Set API Key (if needed)
Local models typically don't need a key. Remote models do:
- Get API key from your provider
- Enter in ZimX settings
- Key is stored locally and never shared

### Step 4: Choose Model
Specify which model to use:
- **Local**: `llama3`, `mistral`, `codellama`, etc.
- **Remote**: `gpt-4`, `gpt-3.5-turbo`, `claude-3-opus`, etc.

### Step 5: Test Connection
Use the test feature in settings to verify:
- Can connect to server
- API key is valid
- Model responds

## Optional: RAG (Semantic Search)
For AI to search your vault effectively, enable RAG:

1. **Install embedding model** (local approach):
   - Ollama: `ollama pull nomic-embed-text`
   - Or use remote embedding API

2. **Enable in settings**:
   - Toggle "Enable semantic search"
   - Choose embedding model

3. **Index your vault**:
   - ZimX will index all pages
   - Takes a few minutes initially
   - Updates incrementally as you edit

**Why RAG?**
- AI can find relevant pages when answering questions
- Grounds answers in your actual notes
- More accurate and useful responses

## Recommended Models

### For Local Use
**Best all-around**: `llama3` (7B or 13B)
- Good balance of speed and capability
- Works well for most tasks

**For coding**: `codellama`
- Better at code generation and technical content

**Lightweight**: `mistral` (7B)
- Faster on older hardware

### For Remote Use
**Most capable**: `gpt-4` or `claude-3-opus`
- Best for complex reasoning
- Higher cost

**Fast and cheap**: `gpt-3.5-turbo` or `claude-3-haiku`
- Good for simple tasks
- Lower cost

## Troubleshooting Setup

### "Connection Failed"
- Verify the AI server is running
- Check the endpoint URL is correct
- Check firewall settings

### "Invalid API Key"
- Re-enter API key
- Check for spaces or extra characters
- Verify key is active on provider dashboard

### "Model Not Found"
- Check model name spelling
- For local: pull the model first (`ollama pull <model>`)
- For remote: verify model availability

### "Slow Responses"
- Local: model may be too large for your hardware
- Try a smaller model (7B instead of 13B)
- Check CPU/GPU usage

## Privacy Considerations

### Local Models
- Everything stays on your computer
- No data sent anywhere
- Complete privacy

### Remote Models
- Your prompts are sent to the provider
- Subject to provider's privacy policy
- May be used for training (check provider terms)
- Consider using local models for sensitive notes

**Your notes stay yours.** Even with remote AI, your vault files never leave your machine—only the text you explicitly send in prompts.

## Updating Configuration
You can change settings anytime:
- Switch between local and remote
- Change models
- Update API keys
- Enable/disable features

Changes take effect immediately—no restart required.

## Next Steps
Once configured:
- Try the AI chat: [:AI:Chat|AI Chat]
- Use one-shot prompting: [:AI:One_Shot|One-Shot Prompting]
- Create diagrams: [:AI:PlantUML|PlantUML Diagrams]
- Learn the AI philosophy: [:AI|AI]
