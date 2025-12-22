import React, { useEffect, useRef, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import boldIcon from '../assets/bold.svg';
import checkboxIcon from '../assets/checkbox.svg';
import italicIcon from '../assets/italic.svg';
import linkIcon from '../assets/link.svg';
import mindmapIcon from '../assets/mindmap.svg';
import { API_BASE_URL, APIError, apiClient } from '../lib/api';

type SaveStatus = 'idle' | 'saving' | 'saved' | 'error';

const CACHE_PREFIX = 'zimx.page.cache.';
const SERVER_PREFIX = 'zimx.page.server.';

type HomePageProps = {
  headerLeft?: React.ReactNode;
  onLogout?: () => void;
};

export const HomePage: React.FC<HomePageProps> = ({ headerLeft, onLogout }) => {
  const [recentPages, setRecentPages] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [activePath, setActivePath] = useState('');
  const [content, setContent] = useState('');
  const [serverContent, setServerContent] = useState<string | null>(null);
  const [loadingPage, setLoadingPage] = useState(false);
  const [hydrating, setHydrating] = useState(false);
  const [currentRev, setCurrentRev] = useState<number | null>(null);
  const [serverRev, setServerRev] = useState<number | null>(null);
  const [currentMtime, setCurrentMtime] = useState<number | null>(null);
  const [serverMtime, setServerMtime] = useState<number | null>(null);
  const [conflict, setConflict] = useState(false);
  const [conflictContent, setConflictContent] = useState<string | null>(null);
  const [conflictRev, setConflictRev] = useState<number | null>(null);
  const [conflictMtime, setConflictMtime] = useState<number | null>(null);
  const [attachments, setAttachments] = useState<string[]>([]);
  const [saveStatus, setSaveStatus] = useState<SaveStatus>('idle');
  const [error, setError] = useState('');
  const [searchQuery, setSearchQuery] = useState('');
  const [searchResults, setSearchResults] = useState<any[]>([]);
  const [searchLoading, setSearchLoading] = useState(false);
  const [tags, setTags] = useState<string[]>([]);
  const [tagMatches, setTagMatches] = useState<string[]>([]);
  const [tagQuery, setTagQuery] = useState<string | null>(null);
  const [isEditing, setIsEditing] = useState(false);
  const editorRef = useRef<HTMLTextAreaElement | null>(null);
  const saveTimeoutRef = useRef<number | null>(null);
  const contentRef = useRef(content);
  const serverContentRef = useRef(serverContent);
  const serverRevRef = useRef(serverRev);
  const serverMtimeRef = useRef(serverMtime);
  const toolbarButtonStyle: React.CSSProperties = {
    padding: '6px 10px',
    border: '1px solid #222',
    borderRadius: '6px',
    backgroundColor: '#222',
    color: '#fff',
    cursor: 'pointer'
  };
  const toolbarIconStyle: React.CSSProperties = {
    width: '18px',
    height: '18px',
    filter: 'brightness(0) invert(1)'
  };
  const checkboxRegex = /^\s*\(( |x|X)\)\s+(.*)$/;

  const formatPagePath = (path: string) => {
    if (!path) return '';
    const parts = path.split('/').filter(Boolean);
    if (parts.length === 0) return '';
    const last = parts[parts.length - 1];
    parts[parts.length - 1] = last.endsWith('.txt') ? last.slice(0, -4) : last;
    return parts.join(':');
  };

  const pageName = activePath ? formatPagePath(activePath) : '';

  const resolveVaultPath = (basePath: string, src: string) => {
    if (!src) return '';
    if (src.startsWith('http://') || src.startsWith('https://') || src.startsWith('data:') || src.startsWith('blob:')) {
      return src;
    }
    const cleanSrc = src.replace(/\\/g, '/');
    if (cleanSrc.startsWith('/')) {
      return cleanSrc;
    }
    const baseParts = basePath.split('/').filter(Boolean);
    baseParts.pop();
    const combined = [...baseParts, ...cleanSrc.split('/')];
    const stack: string[] = [];
    for (const part of combined) {
      if (!part || part === '.') continue;
      if (part === '..') {
        stack.pop();
        continue;
      }
      stack.push(part);
    }
    return `/${stack.join('/')}`;
  };

  const buildFileUrl = (vaultPath: string) => {
    if (!vaultPath) return '';
    return `${API_BASE_URL}/api/file/raw?path=${encodeURIComponent(vaultPath)}`;
  };

  const resolveWikiTarget = (raw: string) => {
    const trimmed = raw.trim();
    const normalized = trimmed.startsWith(':') ? trimmed.slice(1) : trimmed;
    const clean = normalized.replace(/\\/g, '/');
    if (clean.startsWith('/')) {
      return clean;
    }
    return `/${clean}`;
  };

  const markdownComponents = {
    img: ({ src, alt }: { src?: string; alt?: string }) => {
      const vaultPath = resolveAttachmentPath(src || '');
      const imageSrc = buildFileUrl(vaultPath);
      return (
        <img
          src={imageSrc}
          alt={alt || ''}
          loading="lazy"
          decoding="async"
          style={{ maxWidth: '100%', borderRadius: '6px' }}
        />
      );
    },
    a: ({ href, children }: { href?: string; children?: React.ReactNode }) => {
      if (!href) {
        return <span>{children}</span>;
      }
      if (href.startsWith(':') || href.startsWith('/')) {
        const targetPath = resolveWikiTarget(href);
        return (
          <button
            type="button"
            onClick={() => openPage(targetPath)}
            style={{
              background: 'none',
              border: 'none',
              padding: 0,
              color: '#7db7ff',
              textDecoration: 'underline',
              cursor: 'pointer'
            }}
          >
            {children}
          </button>
        );
      }
      return (
        <a href={href} target="_blank" rel="noreferrer">
          {children}
        </a>
      );
    },
  };

  const resolveAttachmentPath = (src: string) => {
    const normalized = resolveVaultPath(activePath, src);
    if (!normalized) return '';
    if (attachments.includes(normalized)) {
      return normalized;
    }
    const fileName = normalized.split('/').pop() || '';
    if (fileName) {
      const match = attachments.find((path) => path.endsWith(`/${fileName}`));
      if (match) {
        return match;
      }
    }
    return normalized;
  };

  useEffect(() => {
    loadRecent();
    loadTags();
  }, []);

  const loadRecent = async () => {
    try {
      const result = await apiClient.getRecent(10);
      setRecentPages(result.pages);
    } catch (error) {
      console.error('Failed to load recent pages:', error);
    } finally {
      setLoading(false);
    }
  };

  const runSearch = async (query: string) => {
    const trimmed = query.trim();
    if (!trimmed) {
      setSearchResults([]);
      return;
    }
    setSearchLoading(true);
    try {
      const result = await apiClient.search(trimmed);
      setSearchResults(result.results || []);
    } catch (err: any) {
      setError(err.message || 'Search failed');
    } finally {
      setSearchLoading(false);
    }
  };

  const loadTags = async () => {
    try {
      const result = await apiClient.getTags();
      const extracted = result.tags.map((item) => item.tag);
      setTags(extracted);
    } catch (err) {
      console.error('Failed to load tags:', err);
    }
  };

  const updateTagMatches = (text: string, cursor: number) => {
    const prefix = text.slice(0, cursor);
    const match = prefix.match(/(^|\s)@([\w-]*)$/);
    if (!match) {
      setTagQuery(null);
      setTagMatches([]);
      return;
    }
    const query = match[2] || '';
    const filtered = tags.filter((tag) => tag.startsWith(query)).slice(0, 6);
    setTagQuery(query);
    setTagMatches(filtered);
  };

  const openPage = async (path: string) => {
    if (!path) return;
    setError('');
    setActivePath(path);
    setLoadingPage(true);
    setHydrating(true);
    setSaveStatus('idle');
    setServerContent(null);
    setCurrentRev(null);
    setServerRev(null);
    setCurrentMtime(null);
    setServerMtime(null);
    setConflict(false);
    setConflictContent(null);
    setConflictRev(null);
    setConflictMtime(null);
    setAttachments([]);
    setIsEditing(false);
    const cacheKey = `${CACHE_PREFIX}${path}`;
    const cached = localStorage.getItem(cacheKey);
    if (cached !== null) {
      setContent(cached);
    } else {
      setContent('');
    }
    try {
      const result = await apiClient.readPage(path);
      const nextRev = typeof result.rev === 'number' ? result.rev : null;
      const nextMtime = typeof result.mtime_ns === 'number' ? result.mtime_ns : null;
      setServerContent(result.content);
      setCurrentRev(nextRev);
      setServerRev(nextRev);
      setCurrentMtime(nextMtime);
      setServerMtime(nextMtime);
      if (cached === null) {
        setContent(result.content);
      }
      try {
        const attachmentResult = await apiClient.listAttachments(path);
        const attachmentPaths = (attachmentResult.attachments || []).map((item) => item.attachment_path);
        setAttachments(attachmentPaths);
      } catch (err) {
        console.error('Failed to load attachments:', err);
      }
    } catch (err: any) {
      setError(err.message || 'Failed to load page');
    } finally {
      setLoadingPage(false);
      setHydrating(false);
    }
  };


  const applyWrap = (before: string, after: string) => {
    const textarea = editorRef.current;
    if (!textarea) return;
    const start = textarea.selectionStart || 0;
    const end = textarea.selectionEnd || 0;
    const selected = content.slice(start, end);
    const next = `${content.slice(0, start)}${before}${selected}${after}${content.slice(end)}`;
    setContent(next);
    const cursorStart = start + before.length;
    const cursorEnd = cursorStart + selected.length;
    requestAnimationFrame(() => {
      textarea.focus();
      textarea.setSelectionRange(cursorStart, cursorEnd);
    });
  };

  const insertAtCursor = (value: string) => {
    const textarea = editorRef.current;
    if (!textarea) return;
    const start = textarea.selectionStart || 0;
    const end = textarea.selectionEnd || 0;
    const next = `${content.slice(0, start)}${value}${content.slice(end)}`;
    setContent(next);
    const cursor = start + value.length;
    requestAnimationFrame(() => {
      textarea.focus();
      textarea.setSelectionRange(cursor, cursor);
    });
  };

  const handleTagInsert = (tag: string) => {
    const textarea = editorRef.current;
    if (!textarea) return;
    const cursor = textarea.selectionStart || 0;
    const prefix = content.slice(0, cursor);
    const match = prefix.match(/(^|\s)@([\w-]*)$/);
    if (!match) return;
    const replaceStart = cursor - (match[2].length + 1);
    const next = `${content.slice(0, replaceStart)}${tag}${content.slice(cursor)}`;
    setContent(next);
    const newCursor = replaceStart + tag.length;
    requestAnimationFrame(() => {
      textarea.focus();
      textarea.setSelectionRange(newCursor, newCursor);
    });
  };

  useEffect(() => {
    if (!activePath) return;
    if (hydrating || loadingPage) return;
    if (conflict) return;
    if (saveTimeoutRef.current) {
      window.clearTimeout(saveTimeoutRef.current);
    }
    const cacheKey = `${CACHE_PREFIX}${activePath}`;
    localStorage.setItem(cacheKey, content);
    updateTagMatches(content, editorRef.current?.selectionStart || 0);
    saveTimeoutRef.current = window.setTimeout(async () => {
      try {
        setSaveStatus('saving');
        const ifMatchValue = currentMtime !== null ? `mtime:${currentMtime}` : (currentRev !== null ? `rev:${currentRev}` : undefined);
        const result = await apiClient.writePage(activePath, content, ifMatchValue);
        localStorage.setItem(`${SERVER_PREFIX}${activePath}`, content);
        setSaveStatus('saved');
        if (typeof result.rev === 'number') {
          setCurrentRev(result.rev);
          setServerRev(result.rev);
        }
        if (typeof result.mtime_ns === 'number') {
          setCurrentMtime(result.mtime_ns);
          setServerMtime(result.mtime_ns);
        }
        setServerContent(content);
      } catch (err: any) {
        if (err instanceof APIError && err.status === 409) {
          const detail = err.detail as { current_rev?: number; current_mtime_ns?: number; current_content?: string } | undefined;
          setConflict(true);
          setConflictRev(typeof detail?.current_rev === 'number' ? detail?.current_rev : null);
          setConflictMtime(typeof detail?.current_mtime_ns === 'number' ? detail?.current_mtime_ns : null);
          setConflictContent(detail?.current_content ?? null);
          setSaveStatus('error');
          return;
        }
        console.error('Save failed:', err);
        setSaveStatus('error');
      }
    }, 800);
    return () => {
      if (saveTimeoutRef.current) {
        window.clearTimeout(saveTimeoutRef.current);
      }
    };
  }, [content, activePath, hydrating, loadingPage, conflict, currentRev]);

  useEffect(() => {
    contentRef.current = content;
    serverContentRef.current = serverContent;
    serverRevRef.current = serverRev;
    serverMtimeRef.current = serverMtime;
  }, [content, serverContent, serverRev, serverMtime]);

  useEffect(() => {
    if (!activePath) return;
    let cancelled = false;
    const poll = async () => {
      try {
        const result = await apiClient.readPage(activePath);
        if (cancelled) return;
        const nextRev = typeof result.rev === 'number' ? result.rev : null;
        const nextMtime = typeof result.mtime_ns === 'number' ? result.mtime_ns : null;
        const prevRev = serverRevRef.current;
        const prevMtime = serverMtimeRef.current;
        if (nextMtime !== null && prevMtime !== null) {
          if (nextMtime == prevMtime) {
            return;
          }
        } else if (nextRev === null || nextRev === prevRev) {
          return;
        }
        const localContent = contentRef.current;
        const currentServerContent = serverContentRef.current ?? '';
        if (localContent === currentServerContent) {
          setContent(result.content);
          setServerContent(result.content);
          setCurrentRev(nextRev);
          setServerRev(nextRev);
          setCurrentMtime(nextMtime);
          setServerMtime(nextMtime);
          return;
        }
        setConflict(true);
        setConflictRev(nextRev);
        setConflictMtime(nextMtime);
        setConflictContent(result.content);
        setSaveStatus('error');
      } catch (err) {
        console.error('Polling failed:', err);
      }
    };
    const intervalId = window.setInterval(poll, 5000);
    return () => {
      cancelled = true;
      window.clearInterval(intervalId);
    };
  }, [activePath]);

  const handleReloadFromServer = () => {
    if (!conflictContent) return;
    setContent(conflictContent);
    setServerContent(conflictContent);
    setCurrentRev(conflictRev);
    setServerRev(conflictRev);
    setCurrentMtime(conflictMtime);
    setServerMtime(conflictMtime);
    setConflict(false);
    setConflictContent(null);
    setConflictRev(null);
    setConflictMtime(null);
    setSaveStatus('idle');
  };

  const handleOverwriteServer = async () => {
    if (!activePath) return;
    setSaveStatus('saving');
    try {
      const ifMatchValue = conflictMtime !== null
        ? `mtime:${conflictMtime}`
        : (conflictRev !== null ? `rev:${conflictRev}` : (currentMtime !== null ? `mtime:${currentMtime}` : (currentRev !== null ? `rev:${currentRev}` : undefined)));
      const result = await apiClient.writePage(activePath, content, ifMatchValue);
      if (typeof result.rev === 'number') {
        setCurrentRev(result.rev);
        setServerRev(result.rev);
      }
      if (typeof result.mtime_ns === 'number') {
        setCurrentMtime(result.mtime_ns);
        setServerMtime(result.mtime_ns);
      }
      setServerContent(content);
      setConflict(false);
      setConflictContent(null);
      setConflictRev(null);
      setConflictMtime(null);
      setSaveStatus('saved');
    } catch (err: any) {
      if (err instanceof APIError && err.status === 409) {
        const detail = err.detail as { current_rev?: number; current_mtime_ns?: number; current_content?: string } | undefined;
        setConflict(true);
        setConflictRev(typeof detail?.current_rev === 'number' ? detail?.current_rev : null);
        setConflictMtime(typeof detail?.current_mtime_ns === 'number' ? detail?.current_mtime_ns : null);
        setConflictContent(detail?.current_content ?? null);
        setSaveStatus('error');
        return;
      }
      setSaveStatus('error');
    }
  };

  const toggleCheckboxLine = (lineIndex: number) => {
    const lines = content.split('\n');
    const line = lines[lineIndex] || '';
    const match = line.match(checkboxRegex);
    if (!match) return;
    const checked = match[1].toLowerCase() === 'x';
    lines[lineIndex] = line.replace(checkboxRegex, `(${checked ? ' ' : 'x'}) ${match[2]}`);
    setContent(lines.join('\n'));
  };

  const renderMarkdownBlocks = () => {
    if (!content.trim()) {
      return <ReactMarkdown components={markdownComponents}>*Empty page.*</ReactMarkdown>;
    }
    const transformWikiLinks = (text: string) => {
      return text.replace(/\[:([^\]|]+)\|([^\]]+)\]/g, (_match, target, label) => {
        const cleanedLabel = String(label).replace(/^:/, '');
        const targetPath = resolveWikiTarget(`:${target}`);
        return `[${cleanedLabel}](${targetPath})`;
      });
    };
    const blocks: React.ReactNode[] = [];
    const lines = content.split('\n');
    let buffer: string[] = [];
    const flushBuffer = (key: string) => {
      if (buffer.length === 0) return;
      blocks.push(
        <ReactMarkdown key={key} components={markdownComponents}>
          {transformWikiLinks(buffer.join('\n'))}
        </ReactMarkdown>
      );
      buffer = [];
    };
    lines.forEach((line, index) => {
      if (checkboxRegex.test(line)) {
        flushBuffer(`md-${index}`);
        const match = line.match(checkboxRegex);
        if (!match) return;
        const checked = match[1].toLowerCase() === 'x';
        blocks.push(
          <div
            key={`cb-${index}`}
            onDoubleClick={(event) => {
              event.stopPropagation();
              toggleCheckboxLine(index);
            }}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '10px',
              padding: '6px 0',
              cursor: 'pointer'
            }}
            title="Double tap to toggle"
          >
            <span style={{
              width: '18px',
              height: '18px',
              border: '1px solid #bbb',
              borderRadius: '4px',
              display: 'inline-flex',
              alignItems: 'center',
              justifyContent: 'center',
              fontSize: '12px',
              fontWeight: 600
            }}>
              {checked ? 'x' : ''}
            </span>
            <span>{match[2]}</span>
          </div>
        );
      } else {
        buffer.push(line);
      }
    });
    flushBuffer(`md-${lines.length}`);
    return blocks;
  };

  return (
    <div style={{ padding: '20px', maxWidth: '800px', margin: '0 auto' }}>
      <nav style={{
        display: 'grid',
        gridTemplateColumns: '1fr auto 1fr',
        alignItems: 'center',
        gap: '12px',
        padding: '12px 0',
        borderBottom: '1px solid #ddd'
      }}>
        <div style={{ justifySelf: 'start' }}>
          {headerLeft}
        </div>
        <div style={{
          justifySelf: 'center',
          fontWeight: 600,
          textAlign: 'center'
        }}>
          {pageName || 'No page selected'}
        </div>
        <div style={{ justifySelf: 'end' }}>
          {onLogout && (
            <button
              onClick={onLogout}
              style={{
                padding: '8px 14px',
                backgroundColor: '#222',
                color: '#fff',
                border: 'none',
                borderRadius: '4px',
                cursor: 'pointer'
              }}
            >
              Logout
            </button>
          )}
        </div>
      </nav>
      {error && (
        <div style={{
          marginTop: '16px',
          padding: '12px',
          backgroundColor: '#fee',
          color: '#900',
          borderRadius: '6px'
        }}>
          {error}
        </div>
      )}

      <section style={{ marginTop: '24px' }}>
        <div style={{ marginBottom: '12px' }}>
          <input
            type="search"
            value={searchQuery}
            onChange={(e) => {
              const next = e.target.value;
              setSearchQuery(next);
              runSearch(next);
            }}
            placeholder="Search pages..."
            style={{
              width: '100%',
              padding: '10px',
              fontSize: '16px',
              border: '1px solid #ddd',
              borderRadius: '4px'
            }}
          />
        </div>

        <section style={{ marginTop: '16px' }}>
          <h2>Search Results</h2>
          
          {searchQuery.trim() ? (
            searchLoading ? (
              <p>Searching...</p>
            ) : searchResults.length > 0 ? (
              <ul style={{ listStyle: 'none', padding: 0 }}>
                {searchResults.map((page) => (
                  <li
                    key={page.path || page.page_id}
                    onClick={() => openPage(page.path)}
                    style={{ 
                      padding: '12px', 
                      marginBottom: '8px',
                      border: '1px solid #ddd',
                      borderRadius: '4px',
                      cursor: 'pointer'
                    }}
                  >
                    <div style={{ fontWeight: 'bold' }}>{page.title || formatPagePath(page.path)}</div>
                    <div style={{ fontSize: '14px', color: '#666' }}>
                      {formatPagePath(page.path)}
                    </div>
                  </li>
                ))}
              </ul>
            ) : (
              <p>No matching pages</p>
            )
          ) : (
            <p>Start typing to search.</p>
          )}
        </section>

        {activePath ? (
          <div style={{
            border: '1px solid #ddd',
            borderRadius: '8px',
            padding: '12px',
            marginTop: '16px'
          }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <div style={{ fontWeight: 600 }}>{formatPagePath(activePath) || activePath}</div>
              <div style={{ fontSize: '13px', color: '#666' }}>
                {loadingPage
                  ? 'Loading...'
                  : serverContent !== null && serverContent !== content
                    ? 'Syncing'
                    : saveStatus === 'saving'
                      ? 'Saving...'
                      : saveStatus === 'saved'
                        ? 'Saved'
                        : saveStatus === 'error'
                          ? 'Save failed'
                          : 'Idle'}
              </div>
            </div>
            <div style={{ display: 'flex', gap: '8px', marginTop: '10px' }}>
              <button
                type="button"
                style={{
                  padding: '6px 10px',
                  border: '1px solid #222',
                  borderRadius: '6px',
                  backgroundColor: isEditing ? '#f2f2f2' : '#222',
                  color: isEditing ? '#222' : '#fff',
                  cursor: 'pointer'
                }}
                onClick={() => setIsEditing(false)}
              >
                Read
              </button>
              <button
                type="button"
                style={{
                  padding: '6px 10px',
                  border: '1px solid #222',
                  borderRadius: '6px',
                  backgroundColor: isEditing ? '#222' : '#f2f2f2',
                  color: isEditing ? '#fff' : '#222',
                  cursor: 'pointer'
                }}
                onClick={() => setIsEditing(true)}
              >
                Edit
              </button>
            </div>
            {conflict && (
              <div style={{
                marginTop: '8px',
                padding: '10px',
                backgroundColor: '#fdecea',
                borderRadius: '6px',
                color: '#611a15'
              }}>
                <div style={{ marginBottom: '8px' }}>
                  Server has a newer version. Reload to sync or overwrite with your local copy.
                </div>
                <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
                  <button
                    type="button"
                    style={toolbarButtonStyle}
                    onClick={handleReloadFromServer}
                  >
                    Reload server version
                  </button>
                  <button
                    type="button"
                    style={toolbarButtonStyle}
                    onClick={handleOverwriteServer}
                  >
                    Overwrite with local
                  </button>
                </div>
              </div>
            )}
            {!isEditing ? (
              <div
                onDoubleClick={() => setIsEditing(true)}
                style={{
                marginTop: '12px',
                padding: '12px',
                border: '1px solid #eee',
                borderRadius: '6px',
                backgroundColor: '#0f1115',
                color: '#f2f2f2'
              }}>
                {renderMarkdownBlocks()}
              </div>
            ) : (
              <>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px', marginTop: '12px' }}>
                  <button type="button" style={toolbarButtonStyle} onClick={() => applyWrap('**', '**')} aria-label="Bold">
                    <img src={boldIcon} alt="" style={toolbarIconStyle} />
                  </button>
                  <button type="button" style={toolbarButtonStyle} onClick={() => applyWrap('_', '_')} aria-label="Italic">
                    <img src={italicIcon} alt="" style={toolbarIconStyle} />
                  </button>
                  <button type="button" style={toolbarButtonStyle} onClick={() => applyWrap('[', '](url)')} aria-label="Link">
                    <img src={linkIcon} alt="" style={toolbarIconStyle} />
                  </button>
                  <button type="button" style={toolbarButtonStyle} onClick={() => insertAtCursor('( ) ')} aria-label="Checkbox">
                    <img src={checkboxIcon} alt="" style={toolbarIconStyle} />
                  </button>
                  <button type="button" style={toolbarButtonStyle} onClick={() => applyWrap('[[', ']]')} aria-label="Wiki link">
                    <img src={mindmapIcon} alt="" style={toolbarIconStyle} />
                  </button>
                </div>
                <textarea
                  ref={editorRef}
                  value={content}
                  onChange={(e) => {
                    setContent(e.target.value);
                    updateTagMatches(e.target.value, e.target.selectionStart || 0);
                  }}
                  onClick={(e) => updateTagMatches(e.currentTarget.value, e.currentTarget.selectionStart || 0)}
                  onKeyUp={(e) => updateTagMatches((e.target as HTMLTextAreaElement).value, (e.target as HTMLTextAreaElement).selectionStart || 0)}
                  rows={16}
                  style={{
                    width: '100%',
                    marginTop: '12px',
                    padding: '12px',
                    fontSize: '15px',
                    lineHeight: 1.5,
                    borderRadius: '6px',
                    border: '1px solid #222',
                    fontFamily: 'inherit',
                    backgroundColor: '#0f1115',
                    color: '#f2f2f2'
                  }}
                />
                {tagMatches.length > 0 && tagQuery !== null && (
                  <div style={{
                    marginTop: '8px',
                    display: 'flex',
                    flexWrap: 'wrap',
                    gap: '6px'
                  }}>
                    {tagMatches.map((tag) => (
                      <button
                        key={tag}
                        type="button"
                        onClick={() => handleTagInsert(`@${tag}`)}
                        style={{
                          padding: '6px 10px',
                          borderRadius: '14px',
                          border: '1px solid #ddd',
                          backgroundColor: '#f8f9fa',
                          cursor: 'pointer'
                        }}
                      >
                        @{tag}
                      </button>
                    ))}
                  </div>
                )}
              </>
            )}
          </div>
        ) : (
          <p style={{ color: '#666' }}>Open a page to start editing.</p>
        )}

        <section style={{ marginTop: '16px' }}>
          <h2>Recent Pages</h2>
          
          {loading ? (
            <p>Loading...</p>
          ) : recentPages.length > 0 ? (
            <ul style={{ listStyle: 'none', padding: 0 }}>
              {recentPages.map((page) => (
                <li
                  key={page.page_id}
                  onClick={() => openPage(page.path)}
                  style={{ 
                  padding: '12px', 
                  marginBottom: '8px',
                  border: '1px solid #ddd',
                  borderRadius: '4px',
                  cursor: 'pointer'
                }}>
                  <div style={{ fontWeight: 'bold' }}>{page.title || formatPagePath(page.path)}</div>
                  <div style={{ fontSize: '14px', color: '#666' }}>
                    {formatPagePath(page.path)} • Rev {page.rev}
                  </div>
                </li>
              ))}
            </ul>
          ) : (
            <p>No pages yet</p>
          )}
        </section>
      </section>
    </div>
  );
};
