# Keyboard Shortcuts

ZimX is designed to be keyboard-friendly. This page lists all app shortcuts grouped by functional area.

## Help
- **F1**: Open Documentation (this help vault in a new window)

## Quick Access
- **Ctrl+J**: Jump to page
- **Ctrl+L**: Insert link
- **Ctrl+D**: Insert date
- **Alt+Home**: Home page
- **Ctrl+Tab**: Recent pages switcher (history popup)
- **Ctrl+Shift+Tab**: Heading switcher (heading popup)

## Go Menu (Chorded)
Press `G`, release, then press the second key:

- **G,H**: Home (vault root page)
- **G,T**: Tasks panel
- **G,C**: Calendar panel
- **G,M**: Attachments panel
- **G,L**: Link Navigator panel
- **G,A**: AI Chat panel
- **G,O**: Today's journal entry

## Navigation and Focus
- **Alt+Left**: Back in history
- **Alt+Right**: Forward in history
- **Alt+Up**: Move up one level in the hierarchy
- **Alt+Down**: Move down into selected page
- **Alt+PgUp**: Navigate tree up (leaves only)
- **Alt+PgDown**: Navigate tree down (leaves only)
- **Alt+Home**: Home page
- **Ctrl+Shift+Space**: Toggle focus between tree and editor
- **Ctrl+Tab**: Open history popup (release Ctrl to activate)
- **Ctrl+Shift+Tab**: Open heading popup (release Ctrl to activate)

## History and Heading Switchers
- **Ctrl+Tab**: Open the recent pages popup
- **Ctrl+Tab** again: Cycle through recent pages
- **Ctrl+Shift+Tab**: Cycle in reverse
- **Release Ctrl**: Open the selected page
- **Ctrl+Shift+Tab**: Open the heading picker popup
- **Ctrl+Shift+Tab** again: Cycle headings
- **Release Ctrl**: Jump to the selected heading

## Page and File
- **Ctrl+N**: New page
- **Ctrl+S**: Save (ZimX auto-saves, but this forces a save)
- **F2**: Rename selected page in tree
- **Ctrl+Shift+O**: Open vault in new window
- **Ctrl+R**: Reload current page
- **Ctrl+P**: Print or export page

## Search and Jump
- **Ctrl+F**: Find in page
- **Ctrl+H**: Find and replace
- **Ctrl+Shift+F**: Search the vault
- **Ctrl+J**: Jump to page

## Insert and Links
- **Ctrl+L**: Insert link
- **Ctrl+Shift+L**: Copy current page link
- **Ctrl+D**: Insert date
- **Alt+D**: Open today's journal entry

## View and Layout
- **Ctrl+Shift+B**: Toggle left panel
- **Ctrl+Shift+N**: Toggle right panel
- **Ctrl+1**: Left tab: Vault
- **Ctrl+2**: Left tab: Tags
- **Ctrl+3**: Left tab: Search
- **Ctrl+Alt+F**: Focus mode
- **Ctrl+Alt+A**: Audience mode
- **Ctrl++** / **Ctrl+-**: Zoom in/out
- **Ctrl+.**: Preferences

## Editing
- **Ctrl+Z**: Undo
- **Ctrl+Shift+Z** or **Ctrl+Y**: Redo
- **Ctrl+C**: Copy
- **Ctrl+X**: Cut
- **Ctrl+V**: Paste
- **Ctrl+A**: Select all
- **Ctrl+Shift+Tab**: Heading picker popup

## Formatting (Markdown)
These apply formatting to selected text or the current line:

- **Ctrl+B**: Bold
- **Ctrl+I**: Italic
- **Ctrl+K**: Strikethrough
- **Ctrl+U**: Highlight (`==text==`)
- **Ctrl+T**: Verbatim/inline code (`` `text` ``)
- **Ctrl+1** through **Ctrl+5**: Heading level 1-5
- **Ctrl+7**: Remove heading markers
- **Ctrl+9**: Clear inline formatting

## Lists (Format Menu)
- Bullet list
- Dash list
- Checkbox list
- Clear list formatting

## Tasks
- **Ctrl+\\** or **Ctrl+Backslash**: Focus the Tasks search box
- **F12**: Toggle task state on the current line

## Panels and Dialogs
- **Esc**: Close most dialogs/popups
- **Ctrl+W**: Close window/panel (context-dependent)

## Focus and Audience Mode (Overlay)
When the focus/audience overlay is active:
- **Ctrl+Alt+=**: Increase text size (audience mode)
- **Ctrl+Alt+-**: Decrease text size (audience mode)
- **Ctrl+Alt+H**: Toggle paragraph highlight (audience mode)
- **Ctrl+Alt+S**: Toggle soft auto-scroll (audience mode)
- **Ctrl+Alt+C**: Toggle cursor halo (audience mode)
- **Ctrl+F**: Find
- **Ctrl+H**: Find and replace
- **Ctrl+D**: Insert date

## AI Actions (Optional)
If AI is configured:
- **Ctrl+Shift+A**: Open the AI actions overlay on selected text

See [:AI|AI] for more.

## Customizing Shortcuts
Many shortcuts can be customized in ZimX preferences.

## Vi Navigation Mode (Optional)
Vi mode has two states: navigation mode and insert mode. Press **Esc** to return to navigation mode.

When vi mode is enabled (navigation mode):
- **h j k l**: Left, down, up, right
- **Shift+H** / **Shift+L**: Select left/right
- **Shift+U** / **Shift+N**: Select up/down (line-wise)
- **w** / **b**: Next/previous word
- **0** or **q**: Start of line
- **^**: First non-blank
- **;** / **:**: End of line (with/without selection)
- **$**: End of line
- **gg** / **G**: Top / bottom of document
- **/** / **?**: Find forward / backward
- **n** / **#** / *****: Repeat find / word-under-cursor (back/forward)
- **t**: Heading picker
- **Alt+H/J/K/L**: History navigation (back/forward)

When vi mode is enabled (insert mode):
- **i**: Insert before cursor
- **a**: Insert after cursor
- **o** / **O**: New line below / above
- **x**: Delete selection or character
- **d**: Delete selection or line
- **c**: Copy selection
- **p**: Paste last yank/cut
- **r**: Replace next character
- **u** / **y**: Undo / redo
- **.**: Repeat last edit

## Next Steps
- Learn navigation patterns: [:Navigation|Navigation]
- Master editing features: [:Editing|Editing]
- Understand the design philosophy: [:Design_Philosophy|Design Philosophy]