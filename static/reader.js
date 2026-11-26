const form=document.getElementById("chapterForm");
const urlInput=document.getElementById("chapterUrl");
const button=document.getElementById("translateBtn");
const statusBox=document.getElementById("status");
const pagesBox=document.getElementById("pages");
let busy=false;
let previewObjectUrls=[];
const say=text=>{statusBox.textContent=text||""};
const makeJobId=()=>(crypto.randomUUID?crypto.randomUUID():`${Date.now().toString(36)}-${Math.random().toString(36).slice(2,10)}`).replaceAll("-","").toLowerCase();
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
async function jsonPost(path,payload,{retries=2}={}){
  for(let attempt=0;;attempt+=1){
    try{
      const response=await fetch(path,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)});
      const data=await response.json().catch(()=>{throw Object.assign(new Error("انقطع الخادم أثناء المعالجة (تجاوز حد الوقت). أعد المحاولة وسيُستكمل من حيث توقّف."),{transient:true})});
      if(!response.ok){
        if(response.status>=500&&attempt<retries)throw Object.assign(new Error(data.error||"خطأ مؤقت من الخادم."),{transient:true});
        throw new Error(data.error||"تعذرت معالجة الطلب.");
      }
      return data;
    }catch(error){
      if(error.transient&&attempt<retries){await sleep(1200*(attempt+1));continue}
      throw error;
    }
  }
}
async function processChapter(){
  if(busy)return;
  const url=urlInput.value.trim();
  if(!url)return;
  busy=true;button.disabled=true;pagesBox.replaceChildren();
  const track=document.getElementById("progressTrack");
  const bar=document.getElementById("progressBar");
  const setProgress=(ratio)=>{if(track){track.hidden=false;bar.style.width=`${Math.min(100,Math.max(0,Math.round(ratio*100)))}%`}};
  setProgress(0);
  previewObjectUrls.forEach(source=>URL.revokeObjectURL(source));previewObjectUrls=[];
  try{
    say("جارٍ جلب الفصل...");
    const chapter=await jsonPost("/api/chapter",{url,reading_layout:"vertical"});
    if(!Array.isArray(chapter.pages)||!chapter.pages.length)throw new Error("لم يُعثر على صفحات لهذا الفصل.");
    const pagesWithRegions=[];const allRegions=[];let detectionSeconds=0;
    const detectPage=async(index)=>{
      const page=chapter.pages[index];
      const jobId=makeJobId();
      const ocr=await jsonPost("/api/translate",{image:page.image,referer:url,chapter_title:chapter.title,
        page_index:page.index,job_id:jobId,ocr_only:true,cloud_ocr:true,reading_layout:"vertical",
        series_type:"manhwa",reading_direction:"ltr"});
      return {page,regions:(ocr.regions||[]).map(region=>({...region,page_image:page.image,page_ref:page.index})),
        seconds:Number(ocr.ocr_elapsed_seconds||0)};
    };
    const DETECT_CONCURRENCY=4;
    for(let start=0;start<chapter.pages.length;start+=DETECT_CONCURRENCY){
      const batch=[];
      for(let offset=start;offset<Math.min(start+DETECT_CONCURRENCY,chapter.pages.length);offset++)batch.push(offset);
      setProgress(start/(chapter.pages.length*2));
      say(`جارٍ اكتشاف الفقاعات: الصفحات ${start+1}-${Math.min(start+DETECT_CONCURRENCY,chapter.pages.length)} من ${chapter.pages.length}...`);
      const detected=await Promise.all(batch.map(detectPage));
      for(const result of detected){
        detectionSeconds+=result.seconds;
        pagesWithRegions.push({page:result.page,regions:result.regions});
        allRegions.push(...result.regions);
      }
    }
    if(!allRegions.length)throw new Error("لم يُعثر على نص قابل للترجمة في هذا الفصل.");
    say(`اكتمل الكشف في ${chapter.pages.length} صفحة. Gemini يراجع النصوص ويترجم الفصل على دفعات داخل الخادم...`);
    let translated=null;
    for(let attempt=0;attempt<40;attempt+=1){
      translated=await jsonPost("/api/translate/chapter",{
        chapter_title:chapter.title,referer:url,cloud_ocr:true,regions:allRegions,time_budget:45
      });
      if(!translated.partial)break;
      const progress=translated.progress||{};
      say(progress.stage==="translation"
        ?"اكتملت مراجعة النصوص. جارٍ ترجمة الفصل عبر Gemini..."
        :`جارٍ مراجعة النصوص على الخادم: ${progress.completed_regions??0}/${progress.total_regions??"—"} منطقة...`);
    }
    if(!translated||translated.partial)throw new Error("تعذّر إكمال الترجمة ضمن الوقت المتاح. أعد المحاولة وسيُستكمل من حيث توقّف.");
    const resultById=new Map((translated.translations||[]).map(item=>[item.id,item]));
    const appendOriginalImage=(page,label)=>{
      const image=document.createElement("img");image.className="page";
      image.alt=`الصفحة الأصلية ${label+1}`;image.loading="lazy";image.decoding="async";
      image.src=page.image;pagesBox.append(image);
    };
    let shown=0;let skipped=0;
    const RENDER_CONCURRENCY=4;
    for(let start=0;start<pagesWithRegions.length;start+=RENDER_CONCURRENCY){
      const batch=pagesWithRegions.slice(start,start+RENDER_CONCURRENCY);
      setProgress(0.5+start/(pagesWithRegions.length*2));
      say(`جارٍ رسم العربية: الصفحات ${start+1}-${Math.min(start+RENDER_CONCURRENCY,pagesWithRegions.length)} من ${pagesWithRegions.length}...`);
      const outcomes=await Promise.all(batch.map(async({page,regions},offset)=>{
        const renderRegions=regions.map(region=>{
          const result=resultById.get(region.id)||{};
          return {...region,text:result.source_text||region.text,
            content_category:result.content_category||"",translated:result.translated||""};
        }).filter(region=>region.translated&&/[\u0600-\u06FF]/.test(region.translated));
        const skippedCount=regions.length-renderRegions.length;
        if(!renderRegions.length){
          appendOriginalImage(page,start+offset);
          return {skipped:skippedCount,shown:0};
        }
        try{
          const rendered=await jsonPost("/api/translate/render",{image:page.image,referer:url,regions:renderRegions,reading_layout:"vertical"});
          if(!rendered.rendered_regions){
            appendOriginalImage(page,start+offset);
            return {skipped:skippedCount+renderRegions.length,shown:0};
          }
          const previewResponse=await fetch(rendered.preview_url);
          if(!previewResponse.ok)throw new Error(`تعذر تحميل معاينة الصفحة ${start+offset+1}.`);
          const previewUrl=URL.createObjectURL(await previewResponse.blob());previewObjectUrls.push(previewUrl);
          const image=document.createElement("img");image.className="page";
          image.alt=`الصفحة العربية ${start+offset+1}`;image.loading="lazy";image.decoding="async";
          image.src=previewUrl;pagesBox.append(image);
          return {skipped:skippedCount,shown:1};
        }catch(error){
          if(error.message&&error.message.startsWith("تعذر تحميل معاينة"))throw error;
          appendOriginalImage(page,start+offset);
          return {skipped:skippedCount,shown:0};
        }
      }));
      for(const outcome of outcomes){shown+=outcome.shown;skipped+=outcome.skipped}
    }
    const ocr=translated.ocr_metrics||{};
    const ocrTokens=(ocr.usage||[]).reduce((sum,item)=>sum+(item.totalTokenCount||0),0);
    const fallbackNote=(ocr.regions_fallback||0)?` تعذّر OCR السحابي في ${ocr.regions_fallback} مناطق؛ بقي نصها الأصلي دون ترجمة.`:"";
    say(`اكتملت الترجمة: ${shown} صفحة، ${skipped} منطقة مستبعدة/بلا ترجمة. كشف المناطق: ${detectionSeconds.toFixed(1)}ث. OCR Gemini 3.5: ${ocr.regions_cloud_read||0}/${ocr.regions_expected||0} منطقة، ${ocr.requests_sent||0} طلب، ${ocrTokens} توكن. ترجمة Gemini 3.1: ${translated.translation_requests_sent||0} طلب، ${translated.usage?.totalTokenCount??"—"} توكن. مجموع الطلبات: ${translated.api_requests_sent||0}.${fallbackNote}`);
    if(!shown){const note=document.createElement("p");note.className="empty";note.textContent="لم تُنتج ترجمة عربية صالحة؛ لم تُعرض أي صفحة أصلية.";pagesBox.append(note)}
    setProgress(1);
  }catch(error){say(error.message)}
  finally{busy=false;button.disabled=false;setTimeout(()=>{if(track)track.hidden=true},1600)}
}
form.addEventListener("submit",event=>{event.preventDefault();processChapter()});
urlInput.addEventListener("paste",()=>setTimeout(()=>{if(urlInput.value.trim())processChapter()},80));
async function loadCompatibleSources(){
  const list=document.getElementById("sourceList");
  try{
    const response=await fetch("/api/sources");const data=await response.json();
    if(!response.ok||!Array.isArray(data.sources))throw new Error();
    list.replaceChildren();
    data.sources.filter(source=>source.supports_direct_chapter_import).forEach(source=>{
      const chip=document.createElement("span");chip.className="source-chip";chip.textContent=source.name;list.append(chip);
    });
    if(!list.childElementCount)list.textContent="لا توجد مصادر مفعّلة حالياً.";
  }catch(_error){list.textContent="تعذر تحميل قائمة المصادر."}
}
loadCompatibleSources();
