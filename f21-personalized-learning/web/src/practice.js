import {ref} from 'vue';
export function usePractice({api,user,course,error}) {
 const selected=ref([]),attempt=ref(null),result=ref(null),sending=ref(false),polling=ref(false),message=ref('');
 const history=ref({items:[],total:0,page:1,pageSize:20}),mastery=ref(null),profile=ref(null);
 let activeQuestion=null,generation=0;
 const storageKey=()=>user.value&&activeQuestion?'f21-attempt:'+user.value.userId+':'+activeQuestion.questionId:null;
 function persist(){const key=storageKey();if(key){if(attempt.value)sessionStorage.setItem(key,JSON.stringify(attempt.value));else sessionStorage.removeItem(key);}}
 function resetView(){generation++;activeQuestion=null;selected.value=[];attempt.value=null;result.value=null;polling.value=false;message.value='';mastery.value=null;profile.value=null;history.value={items:[],total:0,page:1,pageSize:20};}
 function open(question,recommendationId=null,pathNodeId=null){
  question={...question,recommendationId,pathNodeId};
  generation++;activeQuestion=question;selected.value=[];result.value=null;attempt.value=null;message.value='';polling.value=false;
  try{attempt.value=JSON.parse(sessionStorage.getItem(storageKey())||'null');}catch{sessionStorage.removeItem(storageKey());}
  if(attempt.value){selected.value=[...attempt.value.body.answer];result.value=attempt.value.result||null;message.value=result.value?'已恢复上次提交，可刷新处理状态。':'上次请求结果未确认，请使用原请求重试。';}
 }
 async function refreshLearning(page=1){
  if(user.value?.role!=='STUDENT'||!course.value)return;
  const userId=user.value.userId,courseId=course.value.course.courseId,ticket=generation;
  const base='/students/'+userId,query='?courseId='+courseId;
  const values=await Promise.all([api(base+'/practice-history'+query+'&page='+page),api(base+'/mastery'+query),api(base+'/profile'+query)]);
  if(user.value?.userId!==userId||course.value?.course.courseId!==courseId||ticket!==generation)return;
  history.value=values[0];mastery.value=values[1];profile.value=values[2];
 }
 async function checkStatus(){
  if(!result.value||polling.value)return;
  const ticket=generation,id=result.value.submissionId;polling.value=true;message.value='作答已保存，正在更新学习状态…';
  try{
   for(let i=0;i<10;i++){
    const current=await api('/practice/submissions/'+id);
    if(ticket!==generation)return;
    result.value=current;attempt.value.result=current;persist();
    if(current.processingStatus==='SUCCEEDED'){message.value='学习状态已更新。';await refreshLearning();return;}
    if(current.processingStatus!=='PENDING'){message.value='判分已保存，学习状态处理失败或待修复。可稍后刷新；不要重复提交来代替重试。';await refreshLearning();return;}
    await new Promise(resolve=>setTimeout(resolve,1000));
   }
   message.value='作答已保存，仍在处理中。请稍后刷新处理状态。';await refreshLearning();
  }catch(e){if(ticket===generation)message.value='无法确认处理状态，作答记录不会因此丢失。请稍后刷新。';}
  finally{if(ticket===generation)polling.value=false;}
 }
 async function submit(){
  if(sending.value||!activeQuestion||result.value)return;
  if(!attempt.value){
   if(!selected.value.length){error.value='请先选择答案';return;}
   attempt.value={key:crypto.randomUUID(),body:{questionId:activeQuestion.questionId,catalogVersion:activeQuestion.catalogVersion,answer:[...selected.value].sort(),...(activeQuestion.recommendationId?{recommendationId:activeQuestion.recommendationId}:{}),...(activeQuestion.pathNodeId?{pathNodeId:activeQuestion.pathNodeId}:{})}};
   persist();
  }
  const ticket=generation;sending.value=true;error.value='';message.value='';
  try{
   const saved=await api('/practice/submissions',attempt.value.body,{'Idempotency-Key':attempt.value.key});
   if(ticket!==generation)return;
   result.value=saved;attempt.value.result=saved;persist();await checkStatus();
  }catch(e){
   if(ticket!==generation)return;
   if([400,403,404,409].includes(e.status)){attempt.value=null;persist();error.value=e.message;message.value='请求未被接受，请重新打开题目检查版本和权限。';}
   else message.value='请求结果未确认。已保留原答案和请求编号，请点击“重试原请求”，不会重复计数。';
  }finally{sending.value=false;}
 }
 function another(){if(!result.value||sending.value||polling.value)return;generation++;attempt.value=null;result.value=null;selected.value=[];message.value='新练习会新增一条作答记录。';persist();}
 return {selected,attempt,result,sending,polling,message,history,mastery,profile,open,submit,checkStatus,another,refreshLearning,resetView};
}
