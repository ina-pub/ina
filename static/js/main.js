// Progressive enhancement only: every action here also works as a plain form or link.

import { enableAdminBar } from './adminbar.js'
import { enableComments } from './comments.js'
import { enableExpandos } from './expando.js'
import { enableMarks } from './marks.js'
import { enableVoting } from './vote.js'

enableVoting(document)
enableComments(document)
enableExpandos(document)
enableMarks(document)
enableAdminBar(document)
