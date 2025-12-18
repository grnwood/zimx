# Zimx Help Vault

## Requirements
In the zimx/help-vault folder is a empty vault.  Inside this folder construct a vault in ZimX format.  This will be a series of files that explain the ZimX application and is self documenting.

In the main application create a "Help" menu at the right of any other menu options.  There will be a menu option under Help called 'Documentation'.  This will also be wired to F1 in the application.

This, when selected, will open a new instance of ZimX with this help-vault loaded. Add this folder to the pyinstaller package [zimx.spec](packaging/zimx.spec) .

## Vault Content
This vault will be a breakdown of the entire zimx application from a users usage perspective.  It should be peppered with the 'design philosphy' of zimx and be very action based on how to use the application.  each functional area of zimx should have a separate page outlining the features.... Editing, Navigating, Filtering, Tasks, Calendar, AI stuff (how to setup how to use, plantuml how to setup how to use,  Attachments, one shot prompting, etc.  Where it makes sense, show off the linkage ability of zimx by relating pages to each other via links (with nice labels).



### Design philsophy
Your notes stay yours
ZimX stores everything locally in plain Markdown and folders. No lock-in, no proprietary cloud, no fear of losing your work.

Works even when the internet doesn’t
Full functionality offline. AI, search, and navigation never disappear just because you’re on a plane or the Wi-Fi is down.

Fast, focused, and distraction-free
Write without clutter when you need to focus—or keep context visible when you’re thinking, teaching, or screen-sharing.

Find anything instantly
Powerful search, backlinks, filters, and AI context help you surface the right note at the right time—even years later.

AI that helps you think, not replace you
Use AI to summarize, rewrite, explain, or organize your notes—only when you ask. Nothing happens behind your back.

No “magic,” no surprises
Every AI action is visible, intentional, and reversible. You always know what changed and why.

Organize your way, not someone else’s
Trees, links, tags, tasks, or all of them together. ZimX adapts to how your brain works instead of forcing a workflow.

Starts simple, grows with you
Use ZimX as a clean note app on day one. Unlock advanced features like templates, filters, and automation only when you’re ready.

Keyboard-friendly for real productivity
Stay in flow with powerful keyboard navigation and editing. No constant mouse hunting.

Built for long-term thinking
ZimX is designed for notes you’ll revisit months or years later—not just quick captures you’ll forget tomorrow.

Your notes remain readable anywhere
Even without ZimX, your notes are just Markdown files. Open them in any editor, anytime.

Power without bloat
Features are modular and focused. ZimX stays fast and responsive, even with large note collections.

Great for work, study, and life
Specs, research, sermons, journaling, project notes, teaching outlines—it all lives comfortably in one system.

Control how much AI you want
Use local models, remote models, or none at all. ZimX never forces an AI subscription mindset.

Designed for thinkers, not influencers
ZimX is about clarity, insight, and understanding—not publishing, likes, or social feeds.

You can grow out of ZimX—and that’s okay
If you ever leave, your notes come with you. No exports, no migrations, no regret.


### More design philosphy
ZimX is for People Who Think in Notes

Your notes. Your machine. Your rules.
ZimX is local-first. Plain Markdown. No lock-in. Ever.

Works offline. Always.
No internet? No problem. Your brain doesn’t need Wi-Fi—neither does ZimX.

Fast enough to stay out of your way.
No lag. No bloat. Just typing, thinking, and moving on.

AI when you want it. Silent when you don’t.
ZimX never “helpfully” rewrites your work. You stay in control.

No magic. No surprises.
Every AI action is explicit, visible, and reversible.

Built for real thinking, not content farming.
This isn’t a publishing tool. It’s a thinking tool.

Start simple. Grow powerful.
Use it like a basic notebook—or unlock serious workflows over time.

Organize your way. Not ours.
Trees. Links. Tasks. Filters. Mix them freely.

Find anything instantly.
Search, backlinks, and AI context work together to surface what matters.

Keyboard-first. Flow-friendly.
If your hands leave the keyboard, something went wrong.

Distraction-free when focus matters.
Strip the chrome. Keep the context. You decide.

Audience mode ... make screensharing better.
Strip the chrome. highlight cursor text... smooth scroll.

Built for years, not weeks.
ZimX is for notes you’ll revisit long after other apps are gone.

Your notes stay readable forever.
They’re just files. Open them anywhere. Anytime.

Power without the mess.
No feature soup. No buried settings. Just composable tools.

Use your own AI—or none at all.
Local models. Remote models. Zero models. Your call.

Leave anytime. Take everything.
No exports. No migrations. No hostage data.

ZimX Isn’t For Everyone

If you want a social feed for your notes

If you want AI to think for you

If you’re fine with cloud lock-in

If you prefer pretty over durable

ZimX won’t be your tool.

But if you want a fast, local, AI-augmented thinking space you actually own—
ZimX probably is.



