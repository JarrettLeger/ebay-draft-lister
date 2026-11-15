export function DescriptionPreview({text}:{text:string}) {
  const blocks=text.trim().replaceAll('\r\n','\n').split(/\n\s*\n/);
  return <details className="description-preview" open><summary>Listing description preview</summary>
    <div>{blocks.map((block,i)=>{
      const lines=block.split('\n');
      if(lines.every(line=>line.startsWith('- ')))return <ul key={i}>{lines.map((line,j)=><li key={j}>{line.slice(2)}</li>)}</ul>;
      return <p key={i} style={{whiteSpace:'pre-wrap'}}>{i===0?<strong>{block}</strong>:block}</p>;
    })}</div>
  </details>;
}
