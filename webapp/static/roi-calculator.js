function calculateROI(inventory){
  var freed=Number(inventory)*0.2;
  var cost=79;
  var roi=cost>0?freed/cost:0;
  return {freed:freed,cost:cost,roi:roi};
}
if(typeof module!=="undefined"&&module.exports) module.exports={calculateROI:calculateROI};
