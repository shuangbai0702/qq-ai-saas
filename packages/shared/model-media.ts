/** Apply source dimensions once. Legacy preview caps do not select model tiers. */
export function modelImageTargetEdge(longest:number):number {
 if(!Number.isSafeInteger(longest)||longest<1)throw new RangeError('Invalid original image dimensions');
 if(longest>1024)return 512;
 if(longest>=512)return 256;
 if(longest>256)return 192;
 if(longest>=128)return 128;
 return longest;
}
