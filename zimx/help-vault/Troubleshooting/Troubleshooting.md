# Troubleshooting

Common issues and solutions for ZimX.

**No magic. No surprises.** When something doesn't work, the cause is usually straightforward.

## General Issues

### ZimX Won't Start
**Symptoms**: Application doesn't launch or crashes immediately.

**Solutions**:
1. Check system requirements (supported OS, dependencies)
2. Look for error messages in terminal/logs
3. Try resetting preferences: delete `~/.zimx/settings/` (backup first)
4. Verify Python/dependency versions (if running from source)
5. Reinstall ZimX

### Vault Won't Open
**Symptoms**: Error when trying to open a vault.

**Solutions**:
1. Verify the folder exists and you have permissions
2. Check that `vault-name.md` exists in the vault root (legacy `.txt` also works)
3. Look for `.zimx/` folder corruption—delete and let ZimX rebuild
4. Try creating a new vault to verify ZimX works
5. Check disk space

### Slow Performance
**Symptoms**: Lag when typing, slow navigation, delayed responses.

**Solutions**:
1. **Large vault**: ZimX handles thousands of pages, but extremely large vaults (50k+ pages) may slow down
   - Try tree filtering to work on a subtree
   - Consider splitting vault
2. **Search/indexing**: Disable semantic search if not needed
3. **AI**: Local models can be slow—use lighter models or remote models
4. **System resources**: Close other applications, check RAM/CPU usage

### Pages Don't Save
**Symptoms**: Changes disappear after restart.

**Solutions**:
1. Check file permissions on vault folder
2. Verify disk isn't full
3. Check for file locks (another app editing the file?)
4. Look for error messages in logs
5. Manually verify file on disk: changes should appear immediately

**Your notes stay yours.** ZimX writes directly to disk—if files don't change, something is blocking writes.

## Editor Issues

### Formatting Not Working
**Symptoms**: Bold, italic, headings don't apply.

**Solutions**:
1. Verify you're using correct Markdown syntax
2. Check cursor position (some commands work on current line)
3. Try selecting text first, then apply format
4. Check keyboard shortcuts haven't been changed
5. Use Format menu instead of shortcuts

### Links Don't Work
**Symptoms**: Clicking link does nothing or shows error.

**Solutions**:
1. Verify link syntax: `[:Page_Name|Label]`
2. Check for spaces (use underscores in target)
3. Verify target page exists (or create it)
4. Try root-relative links (starting with `:`)
5. Check for special characters in page names

See [:Links_and_Backlinks|Links and Backlinks] for link syntax.

### Find/Replace Not Finding Text
**Symptoms**: Search finds nothing even though text is visible.

**Solutions**:
1. Check case sensitivity settings
2. Verify whole word vs. partial match settings
3. Look for hidden characters (copy/paste can introduce weird Unicode)
4. Try search panel instead of in-page find
5. Search the actual file with an external tool to verify

### Vi Mode Stuck
**Symptoms**: Can't type normally, cursor changes shape.

**Solutions**:
- Press `i` to enter insert mode
- Or disable vi mode in preferences
- Check if Escape key is working (might be mapped elsewhere)

## Navigation Issues

### Tree Not Showing All Pages
**Symptoms**: Some pages missing from tree.

**Solutions**:
1. **Check nav filter**: You may have a filter active—clear it
2. Verify folders contain `.md` files (legacy `.txt` works; folders without files don't show)
3. Try refreshing: `Tools → Reindex Vault`
4. Check folder permissions
5. Look for very deep nesting (may be collapsed)

### History Navigation Broken
**Symptoms**: Back/forward buttons don't work.

**Solutions**:
1. Verify you've actually navigated to other pages
2. Check if pages were deleted
3. Try clicking pages directly instead of using history
4. Restart ZimX to reset history

### Bookmarks Not Appearing
**Symptoms**: Bookmarks missing from toolbar.

**Solutions**:
1. Verify bookmark was actually created (check tree context menu)
2. Check if toolbar is visible
3. Try removing and re-adding bookmark
4. Check bookmark file: `~/.zimx/bookmarks.json`

## Task Issues

### Tasks Not Showing in Panel
**Symptoms**: Task panel is empty or missing tasks.

**Solutions**:
1. Verify task syntax: `- [ ] task text`
2. Check spacing: must have space after `]`
3. Verify tasks are in actual pages (not just in temp editor)
4. Try reindexing: `Tools → Reindex Vault`
5. Check filter settings in task panel

### Task Checkboxes Don't Work
**Symptoms**: Can't check/uncheck tasks.

**Solutions**:
1. Try clicking in editor directly
2. Verify file isn't read-only
3. Check task syntax is valid
4. Try toggling in task panel vs. editor

### Task Dates Not Recognized
**Symptoms**: Date filters don't work.

**Solutions**:
1. Use ISO format: `<2025-12-31`
2. Verify date is after task checkbox
3. Check for typos in date
4. Try different date formats

See [:Tasks|Tasks] for task syntax.

## AI Issues

### AI Not Available
**Symptoms**: AI menu options missing or grayed out.

**Solutions**:
1. Check AI is configured in settings
2. Verify API endpoint is correct
3. Test connection in settings
4. Check API key is valid (if using remote)
5. Verify AI server is running (if using local)

See [:AI:Setup|AI Setup] for configuration help.

### AI Responses Are Slow
**Symptoms**: Long wait times for AI responses.

**Solutions**:
1. **Local models**: Try a smaller/faster model
2. **Remote models**: Check internet connection
3. Verify server isn't overloaded
4. Try a different model
5. Consider upgrading hardware (for local)

### AI Gives Poor Results
**Symptoms**: AI responses are unhelpful or incorrect.

**Solutions**:
1. **Be more specific** in your prompts
2. Provide more context (select more text)
3. Try a different model (some are better at certain tasks)
4. Iterate: ask follow-up questions
5. Remember: **AI helps you think, not replace you**

### RAG Not Finding Relevant Pages
**Symptoms**: AI can't find pages you know exist.

**Solutions**:
1. Verify RAG is enabled in settings
2. Check vault is indexed (may take time initially)
3. Try reindexing
4. Verify embedding model is configured
5. Try different search terms

## PlantUML Issues

### Diagrams Don't Render
**Symptoms**: Code blocks shown but no diagram image.

**Solutions**:
1. Verify PlantUML is configured in settings
2. Check endpoint/server is accessible
3. Try online rendering (if offline rendering fails)
4. Verify diagram syntax is valid
5. Check error messages

See [:AI:PlantUML|PlantUML Diagrams] for setup.

### PlantUML Editor Won't Open
**Symptoms**: Error when trying to open editor window.

**Solutions**:
1. Check system has required dependencies
2. Verify window isn't hidden behind other windows
3. Try restarting ZimX
4. Check logs for errors

## Search Issues

### Search Finds Nothing
**Symptoms**: Global search returns no results.

**Solutions**:
1. Verify vault is indexed: `Tools → Reindex Vault`
2. Check search query (try broader terms)
3. Try exact phrases in quotes
4. Verify pages actually contain the text
5. Check search isn't limited by filters

### Search Results Are Wrong
**Symptoms**: Search finds pages that don't seem relevant.

**Solutions**:
1. Search matches any occurrence—check full page content
2. Try more specific search terms
3. Use tag search for more precision
4. Check for word variations (plural, tense, etc.)

See [:Search_and_Filtering|Search and Filtering] for search tips.

## Attachment Issues

### Images Don't Display
**Symptoms**: Broken image in editor.

**Solutions**:
1. Verify image file exists in `attachments/` folder
2. Check image path in Markdown is correct
3. Verify image format is supported
4. Try relative vs. absolute paths
5. Check file permissions

### Can't Paste Images
**Symptoms**: Paste doesn't insert image.

**Solutions**:
1. Verify image is actually in clipboard
2. Try saving image file and linking manually
3. Check clipboard format (some apps copy references, not actual images)
4. Try different source (e.g., screenshot tool)

See [:Attachments|Attachments] for more on image handling.

## Getting More Help

### Check Logs
ZimX writes logs that can help diagnose issues:
- Location: `~/.zimx/logs/` (or similar)
- Look for error messages
- Share logs when reporting bugs

### Verify File System
Since ZimX uses plain files:
- Open vault folder in file manager
- Verify `.md` files exist and are readable (legacy `.txt` works)
- Check with external text editor
- Look for permission issues

**Your notes remain readable anywhere.** If ZimX has issues, your files are still accessible.

### Reset to Defaults
If all else fails:
1. Backup your vault(s)
2. Backup `~/.zimx/` if you want to save settings
3. Delete `~/.zimx/` (or just settings)
4. Restart ZimX
5. Reconfigure as needed

### Report Bugs
If you think you've found a bug:
1. Check if issue is reproducible
2. Note exact steps to reproduce
3. Check logs for errors
4. Report with version info and error details

## Common Gotchas

### 1. Folder Without File
Pages need a `.md` file with the same name as the folder (legacy `.txt` works). Just a folder won't show up.

### 2. Case Sensitivity
Some file systems are case-sensitive. `Page` and `page` are different pages.

### 3. Special Characters
Avoid special characters in page names (except spaces and dashes):
- Good: `My Project`, `my-project`
- Bad: `my/project`, `my:project`

### 4. Tree Filter Active
If you can't find pages, check if tree filtering is on. Clear the filter.

### 5. Vi Mode Confusion
If keyboard behaves oddly, you might be in vi command mode. Press `i` or disable vi mode.

## Next Steps
If you're still having issues:
- Review relevant help pages for your feature
- Check the [:Design_Philosophy|Design Philosophy] to understand expected behavior
- Try creating a minimal test vault to isolate the issue
- Reach out to the community or support channels