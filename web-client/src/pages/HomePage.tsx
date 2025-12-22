import React, { useEffect, useState } from 'react';
import { apiClient } from '../lib/api';

export const HomePage: React.FC = () => {
  const [recentPages, setRecentPages] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    loadRecent();
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

  return (
    <div style={{ padding: '20px', maxWidth: '800px', margin: '0 auto' }}>
      <h1>ZimX Web Client</h1>
      
      <section style={{ marginTop: '30px' }}>
        <h2>Recent Pages</h2>
        
        {loading ? (
          <p>Loading...</p>
        ) : recentPages.length > 0 ? (
          <ul style={{ listStyle: 'none', padding: 0 }}>
            {recentPages.map((page) => (
              <li key={page.page_id} style={{ 
                padding: '12px', 
                marginBottom: '8px',
                border: '1px solid #ddd',
                borderRadius: '4px'
              }}>
                <div style={{ fontWeight: 'bold' }}>{page.title || page.path}</div>
                <div style={{ fontSize: '14px', color: '#666' }}>
                  {page.path} • Rev {page.rev}
                </div>
              </li>
            ))}
          </ul>
        ) : (
          <p>No pages yet</p>
        )}
      </section>
    </div>
  );
};
