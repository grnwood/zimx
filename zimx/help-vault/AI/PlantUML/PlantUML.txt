# PlantUML Diagrams

ZimX can help you create and edit diagrams using PlantUML—a text-based diagramming tool.

**Power without bloat.** Diagrams as code means they're versionable, searchable, and maintainable.

## What Is PlantUML?
PlantUML is a tool that generates diagrams from plain text:

```plantuml
@startuml
Alice -> Bob: Hello
Bob -> Alice: Hi!
@enduml
```

This creates a sequence diagram showing the message exchange.

## Why PlantUML in ZimX?

### Text-Based
- Diagrams are plain text
- Easy to version control
- Search and diff friendly
- **Your notes remain readable anywhere**

### AI-Assisted
- Describe a diagram in natural language
- AI generates PlantUML code
- Edit code or regenerate as needed

### Integrated
- Diagrams live alongside your notes
- Rendered inline
- Part of your vault

## Creating Diagrams

### Method 1: AI Generation
1. Open PlantUML editor window
2. Describe your diagram in natural language:
   ```
   Create a sequence diagram showing user login flow:
   User sends credentials to API, API validates with Database,
   Database returns success, API returns JWT token to User
   ```
3. AI generates PlantUML code
4. Review and edit as needed
5. Render the diagram

### Method 2: Write Manually
If you know PlantUML syntax:
1. Create a code block in your note:
   ````markdown
   ```plantuml
   @startuml
   ... your diagram ...
   @enduml
   ```
   ````
2. Use PlantUML editor for live preview
3. Iterate on the code

### Method 3: Edit Existing
1. Open a page with PlantUML code
2. Click to edit in PlantUML editor
3. Make changes
4. Save back to note

## PlantUML Editor Window
ZimX provides a dedicated PlantUML editor:
- Left: PlantUML code
- Right: Rendered diagram preview
- Updates in real-time as you type

**Fast and focused.** Edit and see results immediately.

## Diagram Types Supported

### Sequence Diagrams
Show interactions between components:
```plantuml
@startuml
User -> API: Login request
API -> Database: Validate credentials
Database --> API: Success
API --> User: JWT token
@enduml
```

### Class Diagrams
Show system structure:
```plantuml
@startuml
class User {
  +id: int
  +name: string
  +login()
}
class Admin extends User {
  +permissions: list
}
@enduml
```

### Activity Diagrams
Show workflows:
```plantuml
@startuml
start
:User opens app;
if (Logged in?) then (yes)
  :Show dashboard;
else (no)
  :Show login screen;
endif
stop
@enduml
```

### Component Diagrams
Show system architecture:
```plantuml
@startuml
[Web UI] --> [API Gateway]
[API Gateway] --> [Auth Service]
[API Gateway] --> [Data Service]
[Data Service] --> [Database]
@enduml
```

### State Diagrams
Show state transitions:
```plantuml
@startuml
[*] --> Idle
Idle --> Processing : Start
Processing --> Idle : Complete
Processing --> Error : Fail
Error --> Idle : Reset
@enduml
```

### Use Case Diagrams
Show user interactions:
```plantuml
@startuml
actor User
User --> (Login)
User --> (Create Note)
User --> (Search)
@enduml
```

## Using AI for Diagrams

### Describe, Don't Code
You don't need to know PlantUML syntax:

**Your description**:
```
Create a component diagram showing a microservices architecture:
- Frontend talks to API Gateway
- API Gateway routes to User Service, Order Service, Payment Service
- All services connect to a shared Message Queue
- User Service and Order Service use separate databases
- Payment Service uses a third-party API
```

**AI generates**:
```plantuml
@startuml
[Frontend] --> [API Gateway]
[API Gateway] --> [User Service]
[API Gateway] --> [Order Service]
[API Gateway] --> [Payment Service]
[User Service] --> [User Database]
[Order Service] --> [Order Database]
[Payment Service] --> [Third-party Payment API]
[User Service] --> [Message Queue]
[Order Service] --> [Message Queue]
[Payment Service] --> [Message Queue]
@enduml
```

### Iterate with AI
If the diagram isn't quite right:
```
Add monitoring and logging components that connect to all services.

Change the arrows to show async communication via message queue.

Add a load balancer in front of the API Gateway.
```

AI updates the diagram based on your feedback.

**AI when you want it. Silent when you don't.** Generate diagrams with AI or write them manually—your choice.

## Embedding Diagrams in Notes
Once you have PlantUML code:

1. Copy the code
2. Paste into your note as a code block:
   ````markdown
   ```plantuml
   @startuml
   ... diagram code ...
   @enduml
   ```
   ````
3. ZimX renders it inline (if rendering is enabled)

**Or** save as a separate file and reference it:
```markdown
See architecture diagram: [system-architecture.puml](attachments/system.puml)
```

## PlantUML Setup
To render diagrams, you need PlantUML installed:

### Option 1: Online Rendering
Use PlantUML's online service (requires internet):
- Configure in ZimX settings
- Diagrams are rendered via plantuml.com

### Option 2: Local PlantUML
Install PlantUML locally (works offline):
1. Install Java (PlantUML runs on JVM)
2. Download plantuml.jar
3. Configure path in ZimX settings

**Works offline. Always.** Local PlantUML means diagrams work without internet.

### Option 3: Kroki
Use Kroki server (local or remote):
- Supports multiple diagram types
- Can run locally in Docker
- Configure endpoint in ZimX settings

## Shortcuts in PlantUML Editor
ZimX provides shortcuts for common PlantUML patterns:
- Quick insertion of common elements
- Templates for diagram types
- Keyboard shortcuts for formatting

Access via the shortcuts panel in the editor.

## Best Practices

### 1. Keep Diagrams Simple
- One concept per diagram
- Don't try to show everything
- Multiple simple diagrams > one complex diagram

### 2. Document Your Diagrams
Add context in your note:
```markdown
# System Architecture

High-level view of the microservices architecture:

```plantuml
... diagram ...
```

**Key points**:
- Services communicate asynchronously via message queue
- Each service has its own database (no shared state)
- API Gateway handles authentication and routing
```

**Built for long-term thinking.** Future you will thank present you for the context.

### 3. Version Control Friendly
Since diagrams are text:
- They diff cleanly in git
- You can see what changed
- Easy to merge changes

### 4. Reuse Patterns
Create template diagrams for common patterns:
- Store in `~/.zimx/templates/`
- Copy and modify as needed

### 5. Link from Multiple Places
Diagrams often relate to multiple pages:
```markdown
# Feature Specification
...
See [:Architecture:System_Overview|system architecture] for context.
```

## Example: Architecture Decision Record
Combine diagrams with notes:

```markdown
# ADR: Adopt Microservices Architecture

## Context
Our monolith is becoming hard to maintain...

## Decision
We'll adopt a microservices architecture.

## Architecture

```plantuml
@startuml
[Frontend] --> [API Gateway]
[API Gateway] --> [User Service]
[API Gateway] --> [Order Service]
...
@enduml
```

## Consequences
- Pro: Independent deployment
- Pro: Technology flexibility
- Con: Operational complexity
- Con: Distributed debugging
```

## Exporting Diagrams
PlantUML can export to:
- PNG (raster image)
- SVG (vector image)
- PDF (for documents)

Use the export feature in the PlantUML editor.

## Next Steps
- Learn AI setup: [:AI:Setup|AI Setup]
- Use AI chat to iterate on diagrams: [:AI:Chat|AI Chat]
- Embed in notes: [:Editing|Editing]
- Link to project pages: [:Links_and_Backlinks|Links and Backlinks]
