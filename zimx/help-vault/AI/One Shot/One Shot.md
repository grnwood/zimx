# One-Shot Prompting

One-shot prompting lets you quickly transform selected text using AI.

**Every AI action is visible, intentional, and reversible.** You choose the action, you see the result, you decide whether to keep it.

## What Is One-Shot?
Unlike chat (which is conversational), one-shot:
- Works on selected text
- Executes a single transformation
- Shows the result immediately
- You choose whether to accept or reject

**Fast enough to stay out of your way.** Perfect for quick edits while writing.

## Opening One-Shot Actions

### Method 1: Keyboard
1. Select text in the editor
2. Press **Ctrl+Shift+A** (or configured shortcut)
3. Choose an action from the overlay

### Method 2: Context Menu
1. Select text in the editor
2. Right-click
3. Choose "AI Actions" (if available)
4. Select an action

### Method 3: Menu
1. Select text
2. Go to menu (Tools or AI)
3. Choose an AI action

## Common One-Shot Actions

### Summarize
Condense text to key points:
```
Before: [long paragraph with multiple ideas]

After: Key points:
- Main idea 1
- Main idea 2
- Main idea 3
```

### Expand
Add detail and context:
```
Before: System uses API for auth.

After: The system uses a RESTful API for authentication. 
Users submit credentials via POST request to /auth/login. 
The server validates and returns a JWT token for subsequent requests.
```

### Rewrite for Clarity
Make text clearer without changing meaning:
```
Before: The thing we talked about in the meeting where John 
said stuff about the project needs to happen soon.

After: Based on John's comments in the meeting, we need to 
complete the project milestone by end of quarter.
```

### Fix Grammar
Correct grammar and spelling:
```
Before: Their going to the store tommorrow its going to be closed.

After: They're going to the store tomorrow; it's going to be closed.
```

### Change Tone
Adjust formality or style:
```
Tone: Professional
Before: Hey we need to fix this ASAP it's totally broken.

After: We need to address this issue promptly as it's currently 
affecting functionality.
```

### Extract Action Items
Pull out tasks from meeting notes:
```
Before: We discussed the project. Sarah will handle design. 
John needs to review the code by Friday. I'll draft the proposal.

After:
- [ ] Design @sarah
- [ ] Code review @john <2025-12-20
- [ ] Draft proposal @me
```

### Format as List
Convert paragraph to structured list:
```
Before: The project requires database setup, API integration, 
frontend development, and testing.

After:
- Database setup
- API integration
- Frontend development
- Testing
```

### Generate Title
Create a title for untitled content:
```
Content: [notes about project meeting discussing timeline 
and deliverables]

Title: Project Timeline and Deliverables Meeting Notes
```

## Custom Prompts
You can define custom one-shot actions:

1. Create a custom prompt template
2. Add to ZimX settings
3. Select text and apply your custom action

**Example custom prompts**:
- "Make more concise"
- "Add examples"
- "Convert to markdown table"
- "Translate to [language]"

## How One-Shot Works

### Step 1: Select Text
Highlight the text you want to transform.

### Step 2: Choose Action
Open the one-shot overlay and pick an action.

### Step 3: AI Processes
AI sends the text + prompt to your configured model (see [:AI:Setup|AI Setup]).

### Step 4: Review Result
AI shows the transformed text:
- You can see original and new versions
- You choose whether to accept

### Step 5: Accept or Reject
- **Accept**: Replaces selected text with AI version
- **Reject**: Keeps original text unchanged
- **Edit**: Modify AI version before accepting

**You always know what changed and why.** Nothing happens automatically.

## One-Shot vs. Chat

**Use One-Shot when**:
- You want a quick transformation
- You know exactly what you want
- You want to edit selected text directly
- You don't need follow-up questions

**Use Chat when** (see [:AI:Chat|AI Chat]):
- You want to iterate
- You need conversation
- You're exploring ideas
- You want context from multiple pages

## Best Practices

### 1. Select the Right Amount of Text
- Too little: AI lacks context
- Too much: Results get unfocused
- Sweet spot: 1-3 paragraphs

### 2. Be Specific About What You Want
If using custom prompts:
- "Make more concise" is vague
- "Reduce to 50 words" is specific

### 3. Review AI Output
**Never blindly accept**:
- Check for accuracy
- Verify it still says what you meant
- Edit as needed

**Built for real thinking, not content farming.** AI helps, but you're still the author.

### 4. Iterate If Needed
If the result isn't quite right:
- Undo (Ctrl+Z)
- Try a different action
- Or switch to chat for iteration

### 5. Learn From AI Edits
Notice patterns in how AI improves your writing:
- Common grammar mistakes
- Clarity improvements
- Structure suggestions

Apply these lessons to future writing.

## Privacy
**What AI sees**:
- The selected text only
- The prompt/action you chose

**What AI doesn't see**:
- The rest of your page
- Other pages
- Your vault structure

**Your notes stay yours.** Only the explicitly selected text is sent.

## Performance Tips
- **Local models** (see [:AI:Setup|AI Setup]): Slower but private
- **Remote models**: Faster but requires internet

For quick edits, remote models respond in seconds. Local models may take 10-30 seconds depending on hardware.

## Keyboard Workflow
For maximum speed:
1. Select text
2. **Ctrl+Shift+A**
3. Start typing action name to filter
4. **Enter** to execute
5. Review and accept/reject

**Keyboard-first. Flow-friendly.** Entire process without touching mouse.

## Next Steps
- Try AI chat for deeper work: [:AI:Chat|AI Chat]
- Learn AI setup: [:AI:Setup|AI Setup]
- Understand AI philosophy: [:AI|AI]
