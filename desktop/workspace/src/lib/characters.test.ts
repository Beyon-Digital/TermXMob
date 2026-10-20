import {describe,it,expect} from 'vitest';
import {characterDimensions,companionClip,frameAt,habitatPosition,type CharacterClip,type CompanionPet} from './characters';

const clip:CharacterClip={loop:true,frames:[70,120,90].map(duration_ms=>({atlas:0,x:0,y:0,width:96,height:112,duration_ms}))};
const pet:CompanionPet={id:'chat',session_id:'chat',task_id:'task',name:'Sprout',state:'idle',pack_id:'termx-robot',pack_revision:1,roaming:true};
describe('character playback',()=>{
 it('bounds tall and wide imported canvases by their longest edge',()=>{expect(characterDimensions({width:16,height:512},96)).toEqual({width:3,height:96,scale:0.1875});expect(characterDimensions({width:512,height:16},96)).toEqual({width:96,height:3,scale:0.1875})});
 it('uses individual frame durations and loops exactly at the duration boundary',()=>{expect([0,69,70,189,190,279,280,350].map(time=>frameAt(clip,time))).toEqual([0,0,1,1,2,2,0,1])});
 it('holds the last frame for a nonlooping celebration',()=>{expect(frameAt({...clip,loop:false},2800)).toBe(2);expect(frameAt(clip,-10)).toBe(0);expect(frameAt(undefined,20)).toBe(0)});
 it('bounces within a habitat after crossing either edge',()=>{expect(habitatPosition(190,1,1000,300,100,30)).toEqual({x:180,direction:-1});expect(habitatPosition(10,-1,1000,300,100,30)).toEqual({x:20,direction:1});expect(habitatPosition(10,-1,1000,80,100,30)).toEqual({x:0,direction:1})});
 it('gives task attention priority over agent emotes',()=>{expect(companionClip({...pet,state:'waiting',action:{id:'one',name:'wave',expires_at:200}},true,100)).toBe('waiting');expect(companionClip({...pet,state:'error',action:{id:'one',name:'sleep',expires_at:200}},false,100)).toBe('error')});
 it('honors bounded emotes and discards expired ones',()=>{expect(companionClip({...pet,action:{id:'one',name:'wave',expires_at:200}},true,100)).toBe('wave');expect(companionClip({...pet,action:{id:'one',name:'wave',expires_at:100}},true,100)).toBe('walk');expect(companionClip({...pet,action:{id:'one',name:'sleep',expires_at:200}},false,100)).toBe('sleeping')});
});
