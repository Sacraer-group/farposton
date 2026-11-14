const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require('node:path').join(__dirname, '../flats/static/contact.js'), 'utf8');
function element(dataset={}) {
  const listeners = new Map();
  return {dataset, hidden:false, textContent:'', value:'',
    addEventListener(type, fn) {listeners.set(type,fn);},
    fire(type) {listeners.get(type)?.({preventDefault(){}});},
    setCustomValidity(value) {this.validation=value;}, reportValidity(){this.reported=true;},
    focus(){this.focused=true;}, showModal(){this.open=true;}, close(){this.open=false;},
  };
}
function setup() {
  const nodes=Object.fromEntries(['form','textarea','[data-contact-success]','[data-contact-address]','[data-contact-offer]','[data-contact-close]','[data-contact-sent-text]'].map(x=>[x,element()]));
  const dialog=element(); dialog.querySelector=s=>nodes[s];
  const buttons=[element({contactAddress:'Тополевая, 30',contactOffer:'farpost:1',contactQuestions:'["Какой залог?"]'}),element({contactAddress:'Другая квартира',contactOffer:'farpost:2',contactQuestions:'[]'})];
  vm.runInNewContext(source,{document:{getElementById:()=>dialog,querySelectorAll:()=>buttons}});
  return {nodes,dialog,buttons};
}
test('demo message is tied to the chosen offer and completes without any network API',()=>{
  const {nodes,dialog,buttons}=setup(); buttons[0].fire('click');
  assert.equal(dialog.open,true); assert.equal(nodes['[data-contact-offer]'].textContent,'Предложение farpost:1');
  assert.match(nodes.textarea.value,/Тополевая, 30/); assert.match(nodes.textarea.value,/Какой залог/);
  nodes.textarea.value='<img src=x onerror=alert(1)>'; nodes.form.fire('submit');
  assert.equal(nodes.form.hidden,true); assert.equal(nodes['[data-contact-success]'].hidden,false);
  assert.equal(nodes['[data-contact-success]'].focused,true);
  assert.equal(nodes['[data-contact-sent-text]'].textContent,'<img src=x onerror=alert(1)>');
  nodes['[data-contact-close]'].fire('click'); assert.equal(dialog.open,false);
  buttons[1].fire('click'); assert.equal(nodes.form.hidden,false); assert.equal(nodes['[data-contact-success]'].hidden,true);
  assert.match(nodes.textarea.value,/Другая квартира/); assert.doesNotMatch(nodes.textarea.value,/Тополевая/);
});
test('whitespace message is rejected, editing clears the error',()=>{
  const {nodes,buttons}=setup(); buttons[0].fire('click'); nodes.textarea.value='  \n'; nodes.form.fire('submit');
  assert.equal(nodes.form.hidden,false); assert.equal(nodes.textarea.reported,true); assert.ok(nodes.textarea.validation);
  nodes.textarea.fire('input'); assert.equal(nodes.textarea.validation,'');
});
