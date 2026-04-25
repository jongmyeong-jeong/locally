import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

export default function SummaryMarkdown({ content }) {
  return (
    <ReactMarkdown remarkPlugins={[remarkGfm]}>
      {content}
    </ReactMarkdown>
  )
}
