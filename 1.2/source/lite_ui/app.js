(() => {
 const $=s=>document.querySelector(s), api=()=>window.pywebview.api;
 let state={}, panel=null, loading=false, refreshing=false, lastLogs='', toastTimer, consentShown=false;
 function toast(message){$('#toast').textContent=message;$('#toast').classList.add('visible');clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('#toast').classList.remove('visible'),3000);}
 function rows(records,target){const nodes=records.map(record=>{const row=document.createElement('div');row.className='log-row '+(record.level==='attention'?'attention':'');const time=document.createElement('time');time.textContent=record.time;const text=document.createElement('span');text.textContent=(state.preview?'预览：':'')+record.message;row.append(time,text);return row;});target.replaceChildren(...nodes);}
 async function refresh(){if(refreshing)return;refreshing=true;try{const value=await api().state();if(value.ok===false){toast(value.message);return;}state=value;$('#version').textContent=state.version;$('#size-info').textContent=state.size_mb?`完整程序目录实测 ${Number(state.size_mb).toFixed(1)} MB。系统共享运行库与运行数据另计。`:'';
  $('#preview-banner').hidden=!(state.preview||state.testing);$('#preview-banner').textContent=state.testing?'离线自检：使用临时账户与本机合成页面，不连接学校、不提交真实打卡。':'离线预览：使用临时账户与本机模拟页面，不连接学校、不提交真实打卡。';document.body.classList.toggle('preview',state.preview||state.testing);
  if(!state.accepted){if(!consentShown){$('#disclaimer-text').textContent=state.disclaimer;$('#disclaimer').showModal();consentShown=true;}return;}
  if($('#disclaimer').open)$('#disclaimer').close();
  const busy=state.busy||state.closing;document.body.classList.toggle('running',busy);document.body.classList.toggle('completed',state.completed_today);
  $('#status').textContent=state.status;$('#status-detail').textContent=state.busy?'操作中的验证码或二次认证请在浏览器处理':state.completed_today?(state.enabled?'今日已完成，定时将继续安排明日任务':'今日任务已确认完成，打卡定时当前暂停'):'账户与登录状态仅在本机加密保存';
  $('#day-label').textContent=state.completed_today?'今日已完成':state.enabled?'每日定时已开启':'每日平安，安心安排';
  $('#schedule-toggle span').textContent=state.enabled?'暂停定时':'开始定时';$('#schedule-toggle').disabled=(!state.enabled&&busy)||Boolean(state.panel);
  $('#timer-toggle').setAttribute('aria-checked',String(state.enabled));$('#timer-toggle').disabled=(!state.enabled&&busy)||Boolean(state.panel);
  $('#startup-toggle').setAttribute('aria-checked',String(state.startup_enabled));$('#startup-toggle').disabled=busy||Boolean(state.panel);
  $('#stop').hidden=!busy;$('#check').disabled=busy||Boolean(state.panel);$('#manual').disabled=busy||Boolean(state.panel);
  for(const button of document.querySelectorAll('[data-panel]'))button.disabled=busy;
  $('#account-name').textContent=state.username||'尚未配置';$('#account-hint').textContent=state.has_account?'账户已保存，密码留空可沿用':'点击设置，保存校园账户和密码';
  $('#schedule-time').textContent=state.settings.start;$('#schedule-hint').textContent=`每 ${state.settings.retry_minutes} 分钟重试，至 ${state.settings.end} 截止`;
  $('#next-label').textContent=state.enabled?'下一次执行':'每日基准时间';$('#next-time').textContent=state.next_time;$('#next-date').textContent=state.next_date;$('#next-rule').textContent=state.enabled?state.next_text:'完成后为次日保存随机延迟';
  $('#location-label').textContent=state.location_label;$('#network-label').textContent=state.network_label;$('#log-count').textContent=`${state.records.length} 条`;
  const logs=JSON.stringify(state.records);if(logs!==lastLogs){rows(state.records,$('#logs'));rows(state.records,$('#all-logs'));lastLogs=logs;}
  if(state.ui_error)toast(state.ui_error);
 }finally{refreshing=false;}}
 async function call(method,...args){const result=await api()[method](...args);if(result&&result.ok===false)throw new Error(result.message||'操作未完成');return result;}
 async function action(name){try{await call('action',name);await refresh();}catch(error){toast(error.message);}}
 async function openPanel(name){if(loading)return;try{const data=await call('begin_panel',name);panel=name;
  for(const section of document.querySelectorAll('[data-fields]'))section.hidden=section.dataset.fields!==name;
  const titles={account:'校园账户',schedule:'每日执行时间',location:'定位设置',network:'校园网设置',startup:'启用开机自启动'};
  $('#panel-title').textContent=titles[name];$('#panel-description').textContent=name==='startup'?'登录 Windows 后在托盘运行':'设置仅保存在本机';$('#form-error').textContent='';$('#password').value='';$('#network-password').value='';
  $('#username').value=data.username||'';$('#start-time').value=data.settings.start;$('#end-time').value=data.settings.end;$('#retry').value=data.settings.retry_minutes;
  document.querySelector(`[name="location-mode"][value="${data.settings.location_mode}"]`).checked=true;$('#coordinates').hidden=data.settings.location_mode!=='manual';$('#latitude').value=data.latitude||'';$('#longitude').value=data.longitude||'';
  $('#network-enabled').checked=data.settings.campus_network_enabled;$('#network-username').value=data.network_username||'';$('#carrier').value=data.carrier||'campus';$('#network-test').hidden=name!=='network';$('#save-panel span').textContent=name==='startup'?'启用自启动':'保存设置';
  for(const input of document.querySelectorAll('#settings-form input'))input.required=false;
  if(name==='account')$('#username').required=true;
  if(name==='schedule')for(const id of ['#start-time','#end-time','#retry'])$(id).required=true;
  $('#settings-dialog').showModal();
 }catch(error){toast(error.message);}}
 function values(){if(panel==='account')return{username:$('#username').value,password:$('#password').value};if(panel==='schedule')return{start:$('#start-time').value,end:$('#end-time').value,retry:Number($('#retry').value)};if(panel==='location')return{mode:document.querySelector('[name="location-mode"]:checked').value,latitude:$('#latitude').value,longitude:$('#longitude').value};if(panel==='network')return{enabled:$('#network-enabled').checked,username:$('#network-username').value,password:$('#network-password').value,carrier:$('#carrier').value};return{};}
 async function save(test=false){if(loading)return;loading=true;$('#save-panel').disabled=$('#network-test').disabled=true;try{const result=await call('save_panel',panel,values(),test);$('#password').value=$('#network-password').value='';panel=null;$('#settings-dialog').close();toast(result.message||'已保存');await refresh();}catch(error){$('#form-error').textContent=error.message;}finally{loading=false;$('#save-panel').disabled=$('#network-test').disabled=false;}}
 window.liteRefresh=refresh;window.liteOpenPanel=openPanel;
 window.addEventListener('pywebviewready',async()=>{
  $('#disclaimer').addEventListener('cancel',event=>event.preventDefault());
  $('#accept').onclick=async()=>{try{await call('consent');$('#disclaimer').close();await refresh();}catch(error){toast(error.message);}};
  for(const button of document.querySelectorAll('[data-window]'))button.onclick=()=>call('window_action',button.dataset.window).catch(error=>toast(error.message));
  for(const button of document.querySelectorAll('[data-panel]'))button.onclick=()=>openPanel(button.dataset.panel);
  $('#schedule-toggle').onclick=$('#timer-toggle').onclick=()=>action('toggle_schedule');$('#stop').onclick=()=>action('stop');$('#check').onclick=()=>action('check');$('#manual').onclick=()=>action('manual');
  $('#startup-toggle').onclick=()=>state.startup_enabled?action('disable_startup'):openPanel('startup');
  $('#settings-form').onsubmit=event=>{event.preventDefault();save(false);};$('#network-test').onclick=()=>save(true);
  for(const input of document.querySelectorAll('[name="location-mode"]'))input.onchange=()=>$('#coordinates').hidden=input.value!=='manual';
  for(const button of document.querySelectorAll('[data-close]'))button.onclick=()=>$('#'+button.dataset.close).close();
  $('#settings-dialog').addEventListener('close',()=>{const closed=panel;panel=null;$('#password').value=$('#network-password').value='';if(closed)call('close_panel',closed).then(refresh).catch(()=>{});});
  $('#show-logs').onclick=()=>$('#logs-dialog').showModal();$('#about').onclick=()=>$('#about-dialog').showModal();
  $('#import').onclick=async()=>{try{const result=await call('choose_import');if(!result.cancelled){$('#import-summary').textContent=`已找到 ${result.files} 个配置文件。`;$('#import-error').textContent='';$('#import-dialog').showModal();}}catch(error){toast(error.message);}};
  $('#import-dialog').addEventListener('close',()=>call('close_panel','import').then(refresh).catch(()=>{}));
  $('#confirm-import').onclick=async()=>{try{const result=await call('confirm_import');$('#import-dialog').close();toast(result.message);await refresh();}catch(error){$('#import-error').textContent=error.message;}};
  await refresh();window.liteReady=true;setInterval(()=>refresh().catch(()=>{}),500);
 });
})();
