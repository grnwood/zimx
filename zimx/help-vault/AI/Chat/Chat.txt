# AI Chat

The AI Chat panel lets you have conversations with an AI assistant that has context from your vault.

**AI when you want it. Silent when you don't.** Chat is always explicit—it only works when you open it and ask.

## Opening the Chat
- **G,A** or `Go → AI Chat`
- Opens the AI Chat panel on the right side

## How It Works

### Basic Chat
Type a message and press Enter:
- AI responds based on your question
- Context from the current page is included automatically
- Conversation history is maintained

### Context Awareness
AI Chat can access:
- **Current page content**: Automatically included
- **Selected text**: If you select text before opening chat
- **Search results**: If RAG is enabled, AI searches your vault
- **Previous messages**: Conversation history for context

**No magic. No surprises.** You can see exactly what context is being sent—it's shown in the chat panel.

## Using Chat Effectively

### 1. Ask About The Current Page
```
Summarize this page in 3 bullet points.

What are the action items mentioned here?

Explain the concept of [topic] from this page.
```

AI sees the page content and can answer directly.

### 2. Ask Across Your Vault
If RAG is enabled (see [:AI:Setup|AI Setup]):
```
What did I write about project management?

Find my notes on the Phoenix project.

When did I last mention talking to Sarah?
```

AI searches your vault and provides context-aware answers.

### 3. Iterate on Ideas
```
Help me organize these bullet points into a clear structure.

Suggest 5 more examples like the ones I listed.

What's missing from this outline?
```

Use chat to refine your thinking through conversation.

### 4. Get Help Understanding
```
Explain this technical concept in simpler terms.

What are the implications of this decision?

How does this relate to [other topic]?
```

Great for processing complex information.

## Chat Features

### Sending Context Explicitly
You can manually add context:
1. Select text in the editor
2. Open chat (G,A)
3. Selected text is included in context
4. Ask your question

### Viewing Context
The chat panel shows what context is being used:
- Current page indicator
- Selected text preview
- Search results used (if RAG)

**Every AI action is visible and intentional.** You always know what the AI sees.

### Clearing Chat
Start a new conversation:
- Use "New Chat" button/action
- Previous conversation is cleared
- Fresh context

### Copying Responses
AI responses are text:
- Copy and paste into your notes
- Edit and refine as needed
- AI doesn't automatically edit your vault

**You always know what changed and why.** AI suggests, you decide.

## Advanced: RAG (Retrieval Augmented Generation)

If RAG is enabled, AI can search your vault:

**How it works**:
1. You ask a question
2. AI searches for relevant pages
3. AI reads those pages
4. AI answers using your content

**Benefits**:
- Answers grounded in your notes
- Rediscover forgotten content
- Connect ideas across pages

**Setup**: See [:AI:Setup|AI Setup] for RAG configuration.

## Good Questions to Ask

### Summarization
```
Summarize this page in one paragraph.

What are the key takeaways?

Give me a TL;DR.
```

### Organization
```
Organize these notes into a clear outline.

Suggest headers for this content.

What's a better structure for these ideas?
```

### Analysis
```
What are the common themes in my project notes?

What connections exist between these ideas?

What am I missing in this analysis?
```

### Extraction
```
List all action items from this page.

Extract the dates and deadlines mentioned.

Find all the people mentioned in this note.
```

### Generation (use sparingly)
```
Suggest 3 more examples like these.

What are related topics I should explore?

Draft an outline for [topic].
```

**Built for real thinking, not content farming.** Use chat to enhance your thinking, not replace it.

## Chat vs. One-Shot

**Use Chat when**:
- You want to iterate
- You need to ask follow-up questions
- You're exploring ideas
- You want context from multiple pages

**Use One-Shot when** (see [:AI:One_Shot|One-Shot Prompting]):
- You want a quick transformation
- You know exactly what you want
- You want to edit selected text directly

## Privacy and Context

**What AI sees**:
- Messages you type
- Current page content (if context enabled)
- Selected text
- Search results (if RAG enabled)

**What AI doesn't see**:
- Your entire vault (unless you explicitly ask about it)
- Other pages (unless RAG finds them relevant)
- Previous conversations (unless in current session)

**Your notes stay yours.** Your vault files never leave your machine. Only the text in chat prompts is sent to the AI.

## Keyboard Shortcuts in Chat
- **Enter**: Send message
- **Shift+Enter**: New line in message
- **Ctrl+L**: Clear/new chat
- **Escape**: Close chat panel

## Next Steps
- Try one-shot prompting: [:AI:One_Shot|One-Shot Prompting]
- Learn about AI setup: [:AI:Setup|AI Setup]
- Understand AI philosophy: [:AI|AI]
