Current:
using alt-; toggles a 'vi like' mode that allows certain navigation keys from home key position.
the mental model here for a 'vi' user is hard to keep track. several of they keys mimic vi type functionality.
this is a good mode for non vi users but still gives some of that home key quick navigation.

Updates:
- preference dialogue:
    allow a new feature under 'non actionable tasks':
    checkbox:
    () enable strict vi mode (always in vi nav mode)
    this defaults to off, when changed it stores in the databse settings.
    if it is off, all keys in vi mode behave as is currently.
    if it is on, adjust to new vi keys as outlined below in the markdown editor.
    NOTE: areas where vi mode are automatically disabled, then re-enabled (pop up dialogues, etc) should still work as is in either vi mode.
- markdown editor
    unless otherwise specified here, keep all other key mappings the same when in vi mode.
    if this new feature for strict vi mode is enabled:
        the current 'vi' indicator in the lower right panel changes.
            this should now be a 'INS' to indicate 'insert mode' or 'navigation mode'
            if in navigation mode is should have no background color.
            if in insert mode it should be highlighted in yellow background.
        when in navigation mode (non INS activated) these navigation keys should be supported:
            h moves left; j moves down; k moves up; l moves right
            w jumps to next word start; b to previous word start; e to word end
            '0' jumps to line start
            '$' to line end
            '^' to first nonblank character
            'g' goes to top of file
            'G' to file bottom
            even if in nav mode, tab and shif-tab should still indent and dedent a line of text or multiple selected lines of text.
        when in vi mode / INS active: these vi keys should be supported for editing:
            'i' enters insert before cursor
            'a' inserts after
            'o'/'O' open new line below/above
            'x' deletes character under cursor
            's' replaces character
            'r' replaces with single typed char
            'd' delete current line
            'u' undoes last change
            '.' repeats previous edit
            Esc ends editing session.

