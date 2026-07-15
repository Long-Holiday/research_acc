// Shared utilities for Daily arXiv AI Enhanced System

function escapeHtml(str) {
  if (!str) return '';
  return str.replace(/[&<>'"]/g, 
    tag => ({
      '&': '&amp;',
      '<': '&lt;',
      '>': '&gt;',
      "'": '&#39;',
      '"': '&quot;'
    }[tag] || tag)
  );
}

// Global utilities namespace
window.utils = {
  escapeHtml: escapeHtml
};
