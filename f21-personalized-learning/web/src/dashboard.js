import {ref} from 'vue';

const EMPTY_PAGE={items:[],page:1,pageSize:20,total:0};

export function useDashboard({api,user,course}) {
 const models=ref(null),experiments=ref(null),weakness=ref(null),recommendationMetrics=ref(null);
 const dashboardBusy=ref(false),dashboardError=ref('');

 function reset(){models.value=null;experiments.value=null;weakness.value=null;recommendationMetrics.value=null;dashboardError.value='';}
 async function loadGlobal(){
  if(!['TEACHER','ADMIN'].includes(user.value?.role))return;
  const [modelPage,experimentPage]=await Promise.all([api('/models?page=1&pageSize=20'),api('/experiments?page=1&pageSize=20')]);
  models.value=modelPage;experiments.value=experimentPage;
 }
 async function loadCourse(){
  if(!course.value||!['TEACHER','ADMIN'].includes(user.value?.role))return;
  const id=course.value.course.courseId;
  const [weaknessData,recommendationData]=await Promise.all([
   api('/courses/'+id+'/weakness-statistics'),api('/courses/'+id+'/recommendation-metrics')
  ]);
  weakness.value=weaknessData;recommendationMetrics.value=recommendationData;
 }
 async function load(){
  if(dashboardBusy.value)return;
  dashboardBusy.value=true;dashboardError.value='';
  try{await Promise.all([loadGlobal(),loadCourse()]);}
  catch(e){dashboardError.value=e.message||'看板数据加载失败，请稍后重试';throw e;}
  finally{dashboardBusy.value=false;}
 }
 function percent(value){return value===null||value===undefined?'暂无':(value*100).toFixed(1)+'%';}
 function shortHash(value){return value?value.slice(0,12)+'…':'—';}
 return {models,experiments,weakness,recommendationMetrics,dashboardBusy,dashboardError,reset,loadGlobal,loadCourse,load,percent,shortHash,EMPTY_PAGE};
}
