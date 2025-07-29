
## Combiner Nodes

### Alternate

alternate tracks from multiple streams

This component takes any number of input streams and generates a single output stream by alternating between each of the input streams. If **fail fast** is set, this component will stop producing any tracks once any input stream stops producing tracks. If **fail fast** is not set, this component will continue to generate tracks until all input sources are exhausted.

Paramaters
fail_fast

### Concatenate

Concatenate tracks from multiple streams

This component takes any number of input streams and produces tracks by retrieving all the tracks from the first stream, followed by all the tracks from the second stream and so on.

### Mix In

mix two input streams

This component allows for more sophisticatd mixing of two streams. Tracks are alternately selected from the red and the green streams based upon the settings 

**Parameters** 

fail fast

initial number of green tracks (required) 

green tracks in a row (required) 

red tracks in a row (required)


### Mixer

Mixes input tracks while maintaining a set of rules.

This component will mix tracks from the various input streams, while maintaining a set of rules that govern how the tracks will be ordered.  
Input streams are on the **green** port, banned tracks are on the **red** port and banned artists are on the **orange** port. If **fail fast** is set, then the order of the input tracks is guaranteed to be preserved and the mixer will stop producing tracks when it is no longer able to guarantee the contraints. If **fail fast** is not set, then the mixer will find the next best track on the next input stream that best fits the current constraints and will continue to produce tracks as long as any stream is producing tracks.

**Parameters** 

de-dup

fail fast

maximum tracks

minimum artist separation

### Random

randomly selects tracks from multiple streams

This component takes any number of input streams and produces tracks by continuously selecting a random input stream and returning the next track from that stream. If **fail fast** is set, this component will stop generating tracks as soon as any of its randomly selected sources stops generating tracks.

**Parameters** 

fail fast

## Orderers

### Reverse

Reverses the order of the tracks in the stream

This component will reverse the order of the input tracks

### Separate Artists

minimizes the number of adjacent songs by the same artist

This component will re-order the input tracks such that the number of adjacent tracks with the same artist is minimized

### Shuffle

performs a weighted shuffle of the tracks in the stream

This component will randomly re-order the input tracks. The amount of re-ordering is controlled by a ** randomness** factor. This factor is a number between zero and one. The closer the factor is to one, the more random the resulting track order, while the closer the factor is to zero, the more the original track order is retained. A factor of .1 will lightly shuffle the input tracks

**Parameters** 
randomness (required)



first / last / all but first / all but last
no longer than / no shorter than

### Sample

randomly sample tracks from the stream

This component will randomly sample up to **count** tracks from the input stream. Sampled tracks may be returned in any order

**Parameters**
count (required)