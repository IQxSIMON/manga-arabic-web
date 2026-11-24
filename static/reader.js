const form=document.getElementById("chapterForm");
const urlInput=document.getElementById("chapterUrl");
const button=document.getElementById("translateBtn");
const statusBox=document.getElementById("status");
const pagesBox=document.getElementById("pages");
let busy=false;
let previewObjectUrls=[];
const say=text=>{statusBox.textContent=text||""};
async function jsonPost(path,payload){
  const response=await fetch(path,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)});
  const data=await response.json();
  if(!response.ok)throw new Error(data.error||"تعذرت معالجة الطلب.");
  return data;
}
async function processChapter(){
  if(busy)return;
  const url=urlInput.value.trim();
  if(!url)return;
  busy=true;button.disabled=true;pagesBox.replaceChildren();
  previewObjectUrls.forEach(source=>URL.revokeObjectURL(source));previewObjectUrls=[];
  try{
    say("جارٍ جلب الفصل...");
    const chapter=await jsonPost("/api/chapter",{url,reading_layout:"vertical"});
    if(!Array.isArray(chapter.pages)||!chapter.pages.length)throw new Error("لم يُعثر على صفحات لهذا الفصل.");
    const pagesWithRegions=[];const allRegions=[];let detectionSeconds=0;
    for(let index=0;index<chapter.pages.length;index++){
      const page=chapter.pages[index];
      say(`الصفحة ${index+1} من ${chapter.pages.length}: جارٍ اكتشاف الفقاعات والنصوص...`);
      const jobId=crypto.randomUUID().replaceAll("-","").toLowerCase();
      const ocr=await jsonPost("/api/translate",{image:page.image,referer:url,chapter_title:chapter.title,
        page_index:page.index,job_id:jobId,ocr_only:true,cloud_ocr:true,reading_layout:"vertical",
        series_type:"manhwa",reading_direction:"ltr"});
      detectionSeconds+=Number(ocr.ocr_elapsed_seconds||0);
      const pageRegions=(ocr.regions||[]).map(region=>({...region,page_image:page.image,page_ref:page.index}));
      pagesWithRegions.push({page,regions:pageRegions});
      allRegions.push(...pageRegions);
    }
    if(!allRegions.length)throw new Error("لم يُعثر على نص قابل للترجمة في هذا الفصل.");
    say(`اكتمل الكشف في ${chapter.pages.length} صفحة. Gemini 3.5 يراجع النصوص على دفعات، ثم Gemini 3.1 يترجم الفصل...`);
    const translated=await jsonPost("/api/translate/chapter",{
      chapter_title:chapter.title,referer:url,cloud_ocr:true,regions:allRegions
    });
    const resultById=new Map((translated.translations||[]).map(item=>[item.id,item]));
    let shown=0;let skipped=0;
    for(let index=0;index<pagesWithRegions.length;index++){
      const {page,regions}=pagesWithRegions[index];
      const renderRegions=regions.map(region=>{
        const result=resultById.get(region.id)||{};
        return {...region,text:result.source_text||region.text,
          content_category:result.content_category||"",translated:result.translated||""};
      }).filter(region=>region.translated&&/[\u0600-\u06FF]/.test(region.translated));
      skipped+=regions.length-renderRegions.length;
      if(!renderRegions.length)continue;
      say(`الصفحة ${index+1} من ${pagesWithRegions.length}: جارٍ تنظيف الصورة ورسم العربية...`);
      const rendered=await jsonPost("/api/translate/render",{image:page.image,referer:url,regions:renderRegions,reading_layout:"vertical"});
      if(!rendered.rendered_regions){skipped+=renderRegions.length;continue;}
      const previewResponse=await fetch(rendered.preview_url);
      if(!previewResponse.ok)throw new Error(`تعذر تحميل معاينة الصفحة ${index+1}.`);
      const previewUrl=URL.createObjectURL(await previewResponse.blob());previewObjectUrls.push(previewUrl);
      const image=document.createElement("img");image.className="page";
      image.alt=`الصفحة العربية ${index+1}`;image.loading="lazy";image.decoding="async";
      image.src=previewUrl;pagesBox.append(image);shown++;
    }
    const ocr=translated.ocr_metrics||{};
    const ocrTokens=(ocr.usage||[]).reduce((sum,item)=>sum+(item.totalTokenCount||0),0);
    const fallbackNote=(ocr.regions_fallback||0)?` تعذّر OCR السحابي في ${ocr.regions_fallback} مناطق؛ بقي نصها الأصلي دون ترجمة.`:"";
    say(`اكتملت الترجمة: ${shown} صفحة، ${skipped} منطقة مستبعدة/بلا ترجمة. كشف المناطق: ${detectionSeconds.toFixed(1)}ث. OCR Gemini 3.5: ${ocr.regions_cloud_read||0}/${ocr.regions_expected||0} منطقة، ${ocr.requests_sent||0} طلب، ${ocrTokens} توكن. ترجمة Gemini 3.1: ${translated.translation_requests_sent||0} طلب، ${translated.usage?.totalTokenCount??"—"} توكن. مجموع الطلبات: ${translated.api_requests_sent||0}.${fallbackNote}`);
    if(!shown){const note=document.createElement("p");note.className="empty";note.textContent="لم تُنتج ترجمة عربية صالحة؛ لم تُعرض أي صفحة أصلية.";pagesBox.append(note)}
  }catch(error){say(error.message)}
  finally{busy=false;button.disabled=false}
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
