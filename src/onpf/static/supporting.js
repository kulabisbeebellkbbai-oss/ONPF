document.querySelectorAll('[data-block-editor]').forEach((editor) => {
  const list=editor.querySelector('[data-block-list]');
  const rename=()=> {
    [...list.children].forEach((block,index)=> {
      block.querySelector('legend').textContent='Content block '+(index+1);
      block.querySelectorAll('[name]').forEach((control)=> {
        control.name=control.name.replace(/block_\d+_/,'block_'+index+'_');
      });
    });
    editor.querySelector('[data-block-count]').value=list.children.length;
  };
  editor.addEventListener('click',(event)=> {
    const block=event.target.closest('[data-block]');
    if(event.target.matches('[data-block-add]') && list.children.length<100) {
      const copy=list.firstElementChild.cloneNode(true);
      copy.querySelectorAll('input,textarea').forEach(c=>c.value='');
      copy.querySelector('select').value='paragraph';
      list.append(copy);
    }
    if(block && event.target.matches('[data-block-remove]') && list.children.length>1) block.remove();
    if(block && event.target.matches('[data-block-up]') && block.previousElementSibling) list.insertBefore(block,block.previousElementSibling);
    if(block && event.target.matches('[data-block-down]') && block.nextElementSibling) list.insertBefore(block.nextElementSibling,block);
    rename();
  });
});
