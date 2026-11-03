'use strict';

const labNames={poster:'海报开场',notebook:'现场手记',interview:'采访专刊'};
const labEscape=value=>String(value||'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])).replace(/\n/g,'<br>');
const labText=value=>String(value||'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let labArticles={};

function photo(block,kind){
  const image='<img src="'+labText(block.src)+'" alt="'+labText(block.text)+'">';
  return '<figure class="story-photo '+kind+'">'+image+'<figcaption>'+labEscape(block.text)+'</figcaption></figure>';
}

function header(doc,kind,firstPhoto){
  const column=labEscape(doc.column),title=labEscape(doc.title),subtitle=labEscape(doc.subtitle),author=labEscape(doc.author);
  if(kind==='poster'){
    const headline=String(doc.title||'').split('\n').map(line=>'<span>'+labText(line)+'</span>').join('');
    return '<header class="story-cover"><div class="poster-top"><span>'+column+'</span><span>一场演出的幕后</span></div><div class="poster-image">'+(firstPhoto?'<img src="'+labText(firstPhoto.src)+'" alt="'+labText(firstPhoto.text)+'"><p class="poster-caption">'+labEscape(firstPhoto.text)+'</p>':'')+'</div><h3>'+headline+'</h3><div class="poster-dek"><p>'+subtitle+'</p><small>'+author+'</small></div></header>';
  }
  if(kind==='notebook'){
    return '<header class="story-cover"><p class="notebook-column">'+column+'</p><h3>'+title+'</h3><p class="notebook-dek">'+subtitle+'</p>'+(firstPhoto?'<div class="taped-photo">'+photo(firstPhoto,'')+'</div>':'')+'<p class="notebook-byline">'+author+'</p></header>';
  }
  return '<header class="story-cover"><div class="journal-masthead"><strong>'+column+'</strong><span>一篇后台采访</span></div><div class="journal-cover-grid"><h3>'+title+'</h3>'+(firstPhoto?'<figure><img src="'+labText(firstPhoto.src)+'" alt="'+labText(firstPhoto.text)+'"><figcaption>'+labEscape(firstPhoto.text)+'</figcaption></figure>':'')+'</div><div class="journal-dek"><p>'+subtitle+'</p><small>'+author+'</small></div></header>';
}

function article(doc,kind){
  const firstIndex=doc.blocks.findIndex(b=>b.type==='image');
  const firstPhoto=doc.blocks[firstIndex];
  let result='<article class="story story-'+kind+'">'+header(doc,kind,firstPhoto)+'<div class="story-body">';
  for(let i=0;i<doc.blocks.length;i++){
    const b=doc.blocks[i];if(i===firstIndex)continue;
    const text=labEscape(b.text);
    if(b.type==='paragraph')result+='<div class="prose"><p>'+text+'</p></div>';
    if(b.type==='heading')result+='<h4><span>'+text+'</span></h4>';
    if(b.type==='quote')result+='<blockquote><p>'+text+'</p></blockquote>';
    if(b.type==='image')result+=photo(b,'body-photo');
    if(b.type==='divider')result+='<div class="story-break" aria-hidden="true"></div>';
    if(b.type==='signature')result+='<footer class="story-signature">'+text+'</footer>';
  }
  result+='</div><aside class="photo-credit">示意配图：<a href="https://commons.wikimedia.org/wiki/File:Open_Seating_(16614957680).jpg" target="_blank" rel="noopener noreferrer">Phil Roeder</a> / <a href="https://creativecommons.org/licenses/by/2.0/" target="_blank" rel="noopener noreferrer">CC BY 2.0</a>。图片仅缩小尺寸；版面中使用了裁切显示。</aside></article>';
  return result;
}

function fit(container){
  const scale=container.clientWidth/390;
  const story=container.querySelector('.story');
  if(!story)return;
  story.style.transform='scale('+scale+')';
  container.querySelector('.scaled-story').style.height=(story.scrollHeight*scale)+'px';
}

function mount(container,html){
  container.innerHTML='<div class="scaled-story">'+html+'</div>';
  const resize=()=>fit(container);
  for(const image of container.querySelectorAll('img'))image.addEventListener('load',resize);
  const observer=new ResizeObserver(resize);observer.observe(container);resize();
  return ()=>observer.disconnect();
}

async function startLab(){
  try{
    const response=await fetch('/api/bootstrap');
    if(!response.ok)throw new Error('文稿未能载入，请确认本地服务正在运行。');
    const {sample}=await response.json();
    for(const kind of Object.keys(labNames)){
      labArticles[kind]=article(sample,kind);
      mount(document.querySelector('#'+kind+'-window'),labArticles[kind]);
    }
    for(const button of document.querySelectorAll('[data-expand]'))button.onclick=()=>{
      const kind=button.dataset.expand,dialog=document.querySelector('#full-dialog'),content=document.querySelector('#full-content');
      document.querySelector('#full-title').textContent=labNames[kind];
      dialog.showModal();const stop=mount(content,labArticles[kind]);content.scrollTop=0;
      dialog.addEventListener('close',stop,{once:true});
    };
  }catch(error){const el=document.querySelector('#load-error');el.textContent=error.message;el.hidden=false;}
}
document.querySelector('#close-full').onclick=()=>document.querySelector('#full-dialog').close();
startLab();
