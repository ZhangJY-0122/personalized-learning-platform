import {ref,watch,nextTick,onUnmounted} from 'vue';
export function useRecommendations({api,user,course,tab,error,openContent}) {
 const recommendations=ref(null),recBusy=ref(false),recMessage=ref(''),resourceDone=ref(false);
 let observer=null,reading=null,epoch=0;
 const identity=()=>user.value?.userId+':'+course.value?.course.courseId;
 function resetRecommendations(){epoch++;observer?.disconnect();recommendations.value=null;reading=null;resourceDone.value=false;recMessage.value='';}
 // Each pending operation retains its exact body and key across refresh/unknown network results.
 async function post(path,body){
  const slot='f21-g3-pending:'+user.value.userId+':'+path+':'+JSON.stringify(body);
  let intent;try{intent=JSON.parse(sessionStorage.getItem(slot)||'null');}catch{sessionStorage.removeItem(slot);}
  if(!intent){intent={key:crypto.randomUUID(),body};sessionStorage.setItem(slot,JSON.stringify(intent));}
  try{const result=await api(path,intent.body,{'Idempotency-Key':intent.key});sessionStorage.removeItem(slot);return result;}
  catch(e){if([400,403,404,409].includes(e.status))sessionStorage.removeItem(slot);throw e;}
 }
 async function recRefresh(){
  if(user.value?.role!=='STUDENT'||!course.value)return;
  const who=identity(),ticket=epoch;
  const value=await api('/students/'+user.value.userId+'/recommendations?courseId='+course.value.course.courseId);
  if(who===identity()&&ticket===epoch)recommendations.value=value;
 }
 async function action(fn){if(recBusy.value)return;recBusy.value=true;error.value='';const ticket=epoch;try{await fn();}catch(e){if(ticket===epoch)error.value=e.message+'。如结果未确认，重试会沿用原请求编号。';}finally{recBusy.value=false;}}
 const generateRecommendations=()=>action(async()=>{const who=identity();await post('/students/'+user.value.userId+'/recommendations/generate',{courseId:course.value.course.courseId});if(who!==identity())return;await recRefresh();recMessage.value='推荐已保存；再次生成将创建新批次。';});
 const refreshRecommendations=()=>action(recRefresh);
 async function signal(item,type){
  const marker='f21-g3-seen:'+user.value.userId+':'+item.recommendationId+':'+type;
  if(sessionStorage.getItem(marker))return;
  await post('/recommendations/'+item.recommendationId+'/feedback',{feedbackType:type});sessionStorage.setItem(marker,'1');
 }
 const recommendationFeedback=(item,type)=>action(async()=>{await post('/recommendations/'+item.recommendationId+'/feedback',{feedbackType:type});await recRefresh();recMessage.value=type==='COMPLETED'?'已记录自报完成；这不是可信学习证据，不改变掌握度。':'反馈已保存。';});
 async function recordResourceView(detail,rec=null,pathNodeId=null){
  if(user.value?.role!=='STUDENT')return;
  const current={id:detail.resourceId,rec,pathNodeId,viewRecorded:false};reading=current;resourceDone.value=false;
  // Content is already visible. Record the click even when the view receipt is lost.
  const outcomes=await Promise.allSettled([
   post('/resources/'+current.id+'/views',rec?{recommendationId:rec}:pathNodeId?{pathNodeId}:{}).then(()=>{current.viewRecorded=true;}),
   rec?signal({recommendationId:rec},'CLICKED'):Promise.resolve()
  ]);
  const failed=outcomes.find(result=>result.status==='rejected');if(failed)throw failed.reason;
 }
 const completeResource=()=>action(async()=>{
  if(!reading||resourceDone.value)return;const current=reading;
  // Retry a lost view response with its original key before attempting completion.
   if(!current.viewRecorded){await post('/resources/'+current.id+'/views',current.rec?{recommendationId:current.rec}:current.pathNodeId?{pathNodeId:current.pathNodeId}:{});current.viewRecorded=true;}
  if(current.rec)await signal({recommendationId:current.rec},'CLICKED');
  await post('/resources/'+current.id+'/completions',current.rec?{recommendationId:current.rec}:current.pathNodeId?{pathNodeId:current.pathNodeId}:{});
  if(reading===current){resourceDone.value=true;recMessage.value='资源完成已保存，仅更新资源进度和偏好，不增加BKT。';}await recRefresh();
 });
 const openRecommendation=item=>action(async()=>{
  if(!item.available)return;
  await openContent(item.itemType==='RESOURCE'?'resources':'questions',item.itemId,item.recommendationId);
  if(item.itemType==='QUESTION')await signal(item,'CLICKED');
 });
 watch([tab,recommendations],async()=>{
  observer?.disconnect();if(tab.value!=='推荐'||!recommendations.value)return;await nextTick();
  observer=new IntersectionObserver(entries=>{for(const entry of entries)if(entry.isIntersecting){observer.unobserve(entry.target);const item=recommendations.value?.items.find(i=>i.recommendationId===entry.target.dataset.recommendationId);if(item)signal(item,'EXPOSED').catch(()=>{recMessage.value='部分曝光记录未确认，刷新推荐列表可重试。';});}},{threshold:.5});
  document.querySelectorAll('[data-recommendation-id]').forEach(el=>observer.observe(el));
 });
 onUnmounted(()=>observer?.disconnect());
 return {recommendations,recBusy,recMessage,resourceDone,resetRecommendations,recRefresh,generateRecommendations,refreshRecommendations,recommendationFeedback,openRecommendation,recordResourceView,completeResource};
}
