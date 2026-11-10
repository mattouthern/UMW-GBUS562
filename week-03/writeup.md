## What I Built
Using Claude Code, I built a RAG tool called ask.py. The tool is able to review and chunk multiple files using a vector database. This allows me to ask specific questions which the tool is able to answer based on the information in the referenced files. In question 1, the zip file contained two versions of the consumer fee schedule - version 7 and version 8 and both were marked current. The information regarding overdraft fees was different in the two versions and, when asking question 1, I got results that pointed to both documents. To resolve the issue I created larkfield-doc.archive folder and moved the older (version 7) file to the archive and had the tool exclude it from the search. This allowed me to return the correct information on overdraft fees - or $29. Question 1 was run twice followed by 5 additional questions referencing the current documents outside of the archive folder. The results of all 7 runs are included as .txt files.  

## Why This Approach
Chunking method: The files in the Larkfield-docs folder are chunked according to structure. They are split by section heading and each section becomes its own chunk. Only very long sections are cut into smaller pieces.

Makes sense on its own: Yes. In reviewing the outputs of my various runs, the chunks make sense on their own. For example, in question 1 (after) I get the following chunk for a response: "When Larkfield pays a check, debit card purchase, or automatic payment that overdraws a member's checking account, the overdraft fee is $29 per item paid. When Larkfield returns an item unpaid because the account does not have enough money, the returned item fee is $29 per item returned. No more than three overdraft or returned item fees are charged to one account in a single business day. No overdraft fee is charged if the account is overdrawn by $10 or less at the end of the business day. Members can link a savings account for overdraft transfers. An overdraft transfer from a linked savings account is free." 

Label and Source: Both. The responses include the source information including the source file (ex. source consumer-fee-schedule.html), section (ex. Consumer Fee Schedule - Overdraft and returned items fees), version #, and owner. 

Short enough: Yes. The responses are short enough that the responses provide complete sections without truncating the end of the answers. 

## Example Run
Rank 1 | score 0.254 | source consumer-fee-schedule.html
Consumer Fee Schedule - Stop payments
Version 8 (current). Replaces Version 7. Owner: Member Services.

A stop payment on a check costs $30 per request. One request can cover a single check or a series of checks with consecutive numbers. A stop payment stays in effect for six months and can be renewed for another $30.

Rank 2 | score 0.245 | source consumer-fee-schedule.html
Consumer Fee Schedule - Checking account monthly fees
Version 8 (current). Replaces Version 7. Owner: Member Services.

Everyday Checking has no monthly service fee. Plus Checking has a monthly service fee of $8. The Plus Checking fee is waived for any month in which the account keeps a minimum daily balance of $1,000, or receives direct deposits totaling $500 or more.

Rank 3 | score 0.244 | source share-certificates.html
Share Certificates - Dividend rates
Version 5 (current). Owner: Deposit Products.

- 6-month share certificate: 3.05% annual percentage yield (APY).
- 12-month share certificate: 3.40% APY.
- 24-month share certificate: 3.55% APY.
- 36-month share certificate: 3.60% APY.
- 60-month share certificate: 3.75% APY.
The rate is fixed when the certificate is opened and does not change during the term. Dividends are paid monthly and can stay in the certificate or be transferred to the member's savings or checking account.

Rank 4 | score 0.183 | source share-certificates.html
Share Certificates - At maturity
Version 5 (current). Owner: Deposit Products.

When a share certificate matures, it renews automatically for the same term at the rate in effect on the renewal day. The member has a 10-day grace period after maturity to withdraw the money or change the term with no penalty.

ANSWER
The provided documents don't contain information about mortgage rates. They only cover stop payment fees, checking account fees, and share certificate dividend rates [consumer-fee-schedule.html] [share-certificates.html].

## Reflection
What was wrong: There were two versions of consumer-fee-schedule, version 7 and version 8. Version 7 indicated that the overdraft fee was $35. Version 8 indicated that the overdraft fee was $29.

How I fixed it: Removed. I removed version 7 from the folder and put it in an archive folder. I rebuild the database without version 7 data included.  

Prevention: Archive Process. I believe that following a diciplined archive process would best address this situation. Wheneven a new version of a document is produced, the superceded version should be removed from the database. 

Question 6: Declined. My tool provided chunks about stop payments, checking account monthly fees, and share certificates before determining that the documents do not provide information on mortgage rates.  
