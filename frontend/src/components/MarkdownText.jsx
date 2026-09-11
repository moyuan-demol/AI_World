function renderInline(text, keyPrefix) {
  const parts = String(text).split('**')
  return parts.map((part, index) => {
    if (index % 2 === 1) {
      return (
        <strong key={keyPrefix + '-' + index} className="font-semibold text-slate-900">
          {part}
        </strong>
      )
    }
    return <span key={keyPrefix + '-' + index}>{part}</span>
  })
}

export default function MarkdownText({ text, className }) {
  const content = String(text || '')
  if (!content.trim()) return null

  const lines = content.split('\n')
  const blocks = []

  lines.forEach((rawLine, index) => {
    const line = rawLine.replace(/\s+$/, '')
    const key = 'line-' + index

    if (!line.trim()) {
      blocks.push(<div key={key} className="h-2" />)
      return
    }
    if (line.startsWith('### ')) {
      blocks.push(
        <h4 key={key} className="mt-2 text-sm font-semibold text-slate-900">
          {renderInline(line.slice(4), key)}
        </h4>,
      )
      return
    }
    if (line.startsWith('## ')) {
      blocks.push(
        <h3 key={key} className="mt-3 text-base font-semibold text-slate-900">
          {renderInline(line.slice(3), key)}
        </h3>,
      )
      return
    }
    if (line.startsWith('# ')) {
      blocks.push(
        <h2 key={key} className="mt-3 text-lg font-semibold text-slate-900">
          {renderInline(line.slice(2), key)}
        </h2>,
      )
      return
    }
    const bullet = line.match(/^\s*[-*]\s+(.*)$/)
    if (bullet) {
      blocks.push(
        <div key={key} className="flex gap-2 pl-1">
          <span className="mt-1 text-brand-500">•</span>
          <span>{renderInline(bullet[1], key)}</span>
        </div>,
      )
      return
    }
    const ordered = line.match(/^\s*(\d+)[.)]\s+(.*)$/)
    if (ordered) {
      blocks.push(
        <div key={key} className="flex gap-2 pl-1">
          <span className="font-medium text-brand-600">{ordered[1]}.</span>
          <span>{renderInline(ordered[2], key)}</span>
        </div>,
      )
      return
    }
    blocks.push(
      <p key={key} className="leading-relaxed">
        {renderInline(line, key)}
      </p>,
    )
  })

  return <div className={'space-y-1 text-sm text-slate-700 ' + (className || '')}>{blocks}</div>
}
