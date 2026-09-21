import {ref} from 'vue';

export function useLearningPath({api,user,course,error,openContent}) {
 const path=ref(null),pathBusy=ref(false),pathMessage=ref('');
 const loadPath=async(version=null)=>{
  if(user.value?.role!=='STUDENT'||!course.value)return;
  const id=course.value.course.courseId;
  path.value=await api('/students/'+user.value.userId+'/learning-path?courseId='+id+(version?('&pathVersion='+version):''));
 };
 const action=async(fn)=>{if(pathBusy.value)return;pathBusy.value=true;error.value='';try{await fn();}catch(e){error.value=e.message||'路径请求失败';}finally{pathBusy.value=false;}};
 const generatePath=()=>action(async()=>{await api('/students/'+user.value.userId+'/learning-path/generate',{courseId:course.value.course.courseId},{'Idempotency-Key':crypto.randomUUID()});await loadPath();pathMessage.value='路径已生成；节点按先修顺序执行。';});
 const openNode=node=>action(async()=>{if(!node.available||!node.actionable)return;await openContent(node.itemType==='RESOURCE'?'resources':'questions',node.itemId,null,node.nodeId);});
 return {path,pathBusy,pathMessage,loadPath,generatePath,openNode};
}
