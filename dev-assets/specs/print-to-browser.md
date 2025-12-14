# Print to Browser and Export to HTML

## Goal
Transform ZimX vault pages to a bundle of relative HTML.
To print, leverage the browser to print a page to PDF.

This will let the browser handle styling, rendering images, etc.

## Use Case 1:
Use Case 1: User is working on a page (or folder page) and wants to print it to PDF for later sharing / printing.

They press control-p or File/Print.

Zimx page:

```
PageA/
    PageA.txt
    attachment.png
```
Resulting OS level browser opens the rendered HTML via
http://localhost/print/PageA?auto=true


```
<html>
<h1>PageA<h1>
(pageA's translated markdown to html)
<a href='./attachment.png'>
```

### Use Case 2:
A user is working on a folder with a series of folders and pages under it and they want to print/export this. This would create one master document placing the subfolder and page content into this one PDF document with the subfolders and pages acting like H2 level headers below it (and so on).

User chooses a option File / Print Folder? (not sure on name)

Zimx page:

```
PageA/P
    PageA.txt
    attachment.png
    PageB/
        PageB.txt
        attachment.png
```
resulting print:

```
<html>
<h1>PageA<h1>
<page a html>
<a href='./attachment.png'>
<h2>PageB</h2>
(pageB's translated markdown to html)
<a href='./Page2/attachment.png'>
```

The translation from markdown to HTML  would happen with a dynamic tempalte rendering system (jinja2 already in?)


