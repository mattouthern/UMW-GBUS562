## What I Built
Using Claude Code, I built a prompt lab that allows me to enter an API key, a system prompt, and a prompt from which to generate a response from Claude. The API key allows the tool to interfact with Claude, as needed, to respond to the request. After creating the prompt lab, I used it to complete four (4) tasks using different prompting techniques - Zero Shot, One Shot, Few Shot, and Chain of Thought. The output of each task was exported and uploaded to my week-02 folder in GitHub.

## Why This Approach
Task 1 - Technique: zero-shot. I used zero-shot for this task because it called for brainstorming to provide 6 new perks for subscribers that stayed at least 6 months that could be refined. Providing examples would bias the responses. This was the best technique for this task specifically because there was no single correct response and I wanted maximum creativity based on the system prompt parameters that were provided. 

Task 2 - Technique: one-shot. Task 2 was to generate bean cards for three coffees. The one-shot technique allowed me to provide explicit instructions on the desired format. The model was able to take this single example and generate responses in the required format. Any additional examples would have just been repetitive and unnecessary. 

Task 3 - Technique: few-shot. Task 3 called for responses that reflected the brand's tone and style, not just format. The best way for the model to understand the desired response was for it to see examples of the tone and style that I was looking for. Adding instructions on format like word count, words to avoid, or whether or not to include emojis allowed the model to format the responses as well as create the desired tone. 

Task 4 - Technique: chain-of-thought. Task 4 was asking for a recommendation, based on logic. Chain-of-thought is the best technique because it calls for the model to describe the reasoning behind the recommendation. This allows for refinement based on the logic. In addition, including company-specific data, in this case data on previous pop ups, allowed the model to base it reasoning and resulting recommendation on company specific data rather than generic data. 

## Example Run
Model: claude-sonnet-5
System
Wildframe Coffee Roasters is a specialty coffee roaster that sells beans online and through a monthly subscription.
Brand voice: warm, plainspoken, and a little irreverent, with no corporate jargon.
Customers: environmentally conscious urban professionals.
You
Here are examples of customer comments and the replies Wilframe posted in the past:
Comment: Is your packaging actually recyclable, or is that just marketing?
Reply: Fair question. The bag is compostable, the little valve isn't, so pop the valve off first. We're working on that last bit.
Comment: Your Kenya roast tasted burnt to me.
Reply: That's not the cup we were going for. Tell us how you brewed it and we'll help you dial it in, or swap you to something mellower.
Comment: Best coffee I've had in years!!
Reply: This made our whole roasting crew's morning. We'll keep doing the thing.
Comment: Can I pause my subscription while I travel?
Reply: Yep. Account, then Subscription, then Pause. Your beans will wait for you, mostly patiently.
Reply to these three customer comments in Wildframe's voice.
Required format: three replies numbered 1 to 3, each no more than 40 words, with no emoji.
My order was supposed to arrive Monday. It's Thursday.
Do you make any decaf that doesn't taste like cardboard?
The Huila is my new Sunday morning ritual. Thank you.
Claude
That's on us, not the mail gremlins. Send us your order number and we'll track it down and make it right, extra fast.
We do. Our decaf Colombia actually tastes like coffee, not regret. Worth a try.
That's exactly the kind of ritual we roast for. Enjoy every cup, Sunday after Sunday.
You
add the comment followed by the reply for each one.
Claude
Comment: My order was supposed to arrive Monday. It's Thursday.
Reply: That's on us, not the mail gremlins. Send us your order number and we'll track it down and make it right, extra fast.
Comment: Do you make any decaf that doesn't taste like cardboard?
Reply: We do. Our decaf Colombia actually tastes like coffee, not regret. Worth a try.
Comment: The Huila is my new Sunday morning ritual. Thank you.
Reply: That's exactly the kind of ritual we roast for. Enjoy every cup, Sunday after Sunday.
You
Number 1 requires the customer to provide additional information (their order number) and this is a public forum. Any order-specific information should be between the customer and Wildframe only. In addition, they are already frustrated so soften the request.
Claude
Comment: My order was supposed to arrive Monday. It's Thursday.
Reply: We're sorry, that's a frustrating wait. Please DM us or email support@wildframecoffee.com so we can track it down and fix this quickly.
You
Can you add something to make up for the inconvenience?
Claude
Comment: My order was supposed to arrive Monday. It's Thursday.
Reply: We're sorry, that's a frustrating wait. Please DM us or email support@wildframecoffee.com so we can track it down, fix this fast, and toss in something extra for the trouble.
You
I like it. Please provide the consolidated list of all 3.
Claude
Comment: My order was supposed to arrive Monday. It's Thursday.
Reply: We're sorry, that's a frustrating wait. Please DM us or email support@wildframecoffee.com so we can track it down, fix this fast, and toss in something extra for the trouble.
Comment: Do you make any decaf that doesn't taste like cardboard?
Reply: We do. Our decaf Colombia actually tastes like coffee, not regret. Worth a try.
Comment: The Huila is my new Sunday morning ritual. Thank you.
Reply: That's exactly the kind of ritual we roast for. Enjoy every cup, Sunday after Sunday.

## Reflection
This was a very interesting exercise, creating the prompt lab and working through tasks 1 - 4. 
What I'm not clear on at this point, is what  the benefit is of using a prompt lab, like the one we built, over simply using the AI tool web interface.
Regarding the prompt techniques, this was most beneficial part of this week's lesson for me. Understanding the benefits and best use cases for each technique is something I can start using in my work today.
In completing tasks 1 through 4, I mirrored the order that the techniques were presented and I felt that each technique was appropriate for the task it was applied to.
That said, I think few-shot could be used to create the perk ideas in task 1. As it was, I had to ask follow up prompts to move the responses in the direction I thought they should go. A few examples may have prevented that. 
Chain of thought made the biggest difference on task 4. I started with a simple prompt then added more detail, including the previous pop up data. The intial response I received was good and logical but once I added the historic data, the output was downright impressive. 