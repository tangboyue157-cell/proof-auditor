/**
 * MarkdownBlock — renders Markdown text with LaTeX math support.
 * Uses react-markdown + remark-math + rehype-katex.
 */
import ReactMarkdown from 'react-markdown';
import remarkMath from 'remark-math';
import rehypeKatex from 'rehype-katex';
import 'katex/dist/katex.min.css';

interface Props {
  children: string;
  className?: string;
}

export default function MarkdownBlock({ children, className }: Props) {
  if (!children) return null;
  
  return (
    <div className={className} style={{ fontSize: 13, lineHeight: 1.7, color: 'var(--text-secondary)' }}>
      <ReactMarkdown
        remarkPlugins={[remarkMath]}
        rehypePlugins={[rehypeKatex]}
        components={{
          // Lean/code blocks with monospace styling
          code({ node, className: codeClass, children: codeChildren, ...props }) {
            const isInline = !codeClass;
            if (isInline) {
              return (
                <code style={{
                  background: 'var(--bg-tertiary)',
                  padding: '1px 5px',
                  borderRadius: 3,
                  fontSize: 12,
                  fontFamily: 'var(--font-mono)',
                  color: 'var(--accent)',
                }} {...props}>
                  {codeChildren}
                </code>
              );
            }
            return (
              <pre className="code-block" style={{ marginTop: 8, marginBottom: 8 }}>
                <code {...props}>{codeChildren}</code>
              </pre>
            );
          },
          // Bold text with primary color
          strong({ children: c }) {
            return <strong style={{ color: 'var(--text-primary)', fontWeight: 600 }}>{c}</strong>;
          },
          // Paragraphs with proper spacing
          p({ children: c }) {
            return <p style={{ marginBottom: 8 }}>{c}</p>;
          },
        }}
      >
        {children}
      </ReactMarkdown>
    </div>
  );
}
