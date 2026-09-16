"""
Copy for the public marketing pages: features, use cases, FAQs and guides.

Kept as data so every page shares one layout and the sitemap can list them all.
Other products are never named; comparisons speak of "other tools".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


@dataclass(frozen=True)
class Section:
    heading: str
    paragraphs: tuple[str, ...] = ()
    bullets: tuple[str, ...] = ()


@dataclass(frozen=True)
class Faq:
    question: str
    answer: str


@dataclass(frozen=True)
class Example:
    """A sample conversation shown on a page."""

    trigger_label: str
    trigger: str
    public_reply: str = ""
    dm: str = ""
    button: str = ""


@dataclass(frozen=True)
class Page:
    slug: str
    icon: str
    #: Short name for cards and links.
    name: str
    #: The page's H1.
    title: str
    #: The <title> tag; aim for under 60 characters.
    seo_title: str
    #: The meta description; aim for under 160 characters.
    description: str
    lead: str
    summary: str
    steps: tuple[tuple[str, str], ...] = ()
    sections: tuple[Section, ...] = ()
    example: Example | None = None
    faqs: tuple[Faq, ...] = ()
    #: Slugs of related feature pages.
    features: tuple[str, ...] = ()


@dataclass(frozen=True)
class Guide:
    slug: str
    title: str
    seo_title: str
    description: str
    summary: str
    published: date
    updated: date
    read_minutes: int
    faqs: tuple[Faq, ...] = field(default_factory=tuple)

    @property
    def template_name(self) -> str:
        return f"marketing/guides/{self.slug}.html"


# --- shared answers -----------------------------------------------------------

FAQ_SAFE = Faq(
    "Is DamDM safe for my Instagram account?",
    "DamDM connects through Meta's official Instagram API, the approved way for businesses to "
    "automate messages. We never ask for your Instagram password and never scrape. Keep your "
    "messages useful and relevant, as Instagram's own rules require, and your account stays in "
    "good standing.",
)
FAQ_ACCOUNT_TYPE = Faq(
    "What kind of Instagram account do I need?",
    "A professional account: either Business or Creator. Switching is free and takes a minute in "
    "the Instagram app under Settings → Account type and tools. Personal accounts can't use "
    "Instagram's messaging API.",
)
FAQ_PRICE = Faq(
    "How much does DamDM cost?",
    "One plan, ₹99 per month, with unlimited automations, DMs and replies. New accounts get a "
    "14-day free trial, and you can cancel any time from Plan & billing.",
)
FAQ_TRIAL = Faq(
    "How does the free trial work?",
    "You approve UPI AutoPay (or a card) when you start, but the first ₹99 is only charged when "
    "the 14 days end. A ₹5 verification charge is taken when you approve AutoPay and refunded "
    "automatically. Cancel before the trial ends and you pay nothing.",
)
FAQ_PASSWORD = Faq(
    "Do you need my Instagram password?",
    "No. You sign in on Instagram's own page and approve access for DamDM. You can remove that "
    "access at any time from DamDM or from Instagram's settings.",
)
FAQ_FACEBOOK = Faq(
    "Do I need a Facebook Page?",
    "No. DamDM uses Instagram login, so a Business or Creator account on its own is enough.",
)
FAQ_PRIVATE_REPLY_LIMIT = Faq(
    "Is there a limit on replying to comments by DM?",
    "Instagram lets a business send one private reply per comment, within 7 days of the comment. "
    "DamDM replies within seconds, so this is rarely a concern.",
)
FAQ_24_HOURS = Faq(
    "Can DamDM message people at any time?",
    "Instagram only allows automated messages as a response to something the person did, such "
    "as commenting, messaging you or replying to your story. Once they reply, you can keep "
    "chatting for 24 hours. DamDM follows these rules, which keeps your account safe.",
)
FAQ_CANCEL = Faq(
    "Can I cancel any time?",
    "Yes. Cancel from Plan & billing in one tap. You won't be charged again, and your plan keeps "
    "working until the end of the period you've paid for.",
)
FAQ_ALTERNATIVE = Faq(
    "How is DamDM different from other Instagram automation tools?",
    "Many tools are priced in US dollars and cost more as your audience grows. DamDM is built "
    "for Indian creators and businesses: one ₹99 monthly plan with everything unlimited, UPI "
    "AutoPay, and a setup that takes about a minute.",
)
FAQ_MULTIPLE_KEYWORDS = Faq(
    "Can one automation listen for several keywords?",
    "Yes. Add as many keywords as you like, for example LINK, PRICE and INFO, and any of them "
    "will start the automation.",
)


# --- features -----------------------------------------------------------------

FEATURES = (
    Page(
        slug="comment-to-dm",
        icon="message",
        name="Comment to DM",
        title="Send a DM automatically when someone comments",
        seo_title="Instagram Comment Auto Reply by DM",
        description=(
            "Auto-DM everyone who comments a keyword on your Instagram post or Reel. Send links, "
            "offers and freebies in seconds. Free for 14 days, then ₹99/month."
        ),
        lead=(
            "Ask people to comment a word like LINK. DamDM replies under the comment and sends "
            "your link straight to their DMs, in seconds, day and night."
        ),
        summary="Reply to comment keywords with a DM that carries your link.",
        steps=(
            ("Pick the post", "Choose one post or Reel, all of them, or your next post."),
            ("Set a keyword", "LINK, GUIDE, PRICE: any word your audience will comment."),
            ("Write the DM", "Add your message and a button with your link. You're live."),
        ),
        sections=(
            Section(
                "Why comment-to-DM works",
                paragraphs=(
                    "Links in captions can't be tapped, and “link in bio” loses people on the "
                    "way. A comment is the easiest thing to ask for, and every comment tells "
                    "Instagram your post is worth showing to more people.",
                    "With comment-to-DM, the person who asked gets the link in their inbox "
                    "straight away, while they're still interested. You get more comments, more "
                    "reach and more clicks from the same post.",
                ),
            ),
            Section(
                "What you can send",
                bullets=(
                    "Product links, discount codes and store pages",
                    "Free guides, PDFs, checklists and lead magnets",
                    "Course, webinar or booking links",
                    "Affiliate links and brand collaborations",
                    "WhatsApp or website links for enquiries",
                ),
            ),
            Section(
                "Built-in touches that help",
                bullets=(
                    "Public replies under the comment, rotated so they don't look copied",
                    "Match one keyword or several, in any capitalisation",
                    "Run it on every post, chosen posts, or your next post automatically",
                    "Combine with Follow to unlock to grow followers at the same time",
                ),
            ),
        ),
        example=Example(
            trigger_label="Comment on your Reel",
            trigger="LINK 🙏",
            public_reply="Sent it to your DMs! 💌",
            dm="Hey! 👋 Thanks for your comment. Here's the link you asked for:",
            button="Get the link",
        ),
        faqs=(FAQ_PRIVATE_REPLY_LIMIT, FAQ_MULTIPLE_KEYWORDS, FAQ_SAFE, FAQ_ACCOUNT_TYPE),
        features=("follow-to-unlock", "public-comment-replies", "dm-keyword-auto-reply"),
    ),
    Page(
        slug="follow-to-unlock",
        icon="lock",
        name="Follow to unlock",
        title="Grow followers by unlocking your link after a follow",
        seo_title="Follow to Unlock: Get Instagram Followers With DMs",
        description=(
            "Ask commenters to follow you before they get your link. DamDM checks the follow and "
            "unlocks the DM automatically. Grow with every post for ₹99/month."
        ),
        lead=(
            "Turn every freebie into new followers. DamDM asks people to follow you first, "
            "checks that they did, then sends the link."
        ),
        summary="Send the link only after they follow you, and grow with every post.",
        steps=(
            ("They comment", "Someone comments your keyword on a post or Reel."),
            (
                "DamDM asks for a follow",
                "They get a DM asking them to follow and tap “I'm following”.",
            ),
            ("Link unlocks", "DamDM checks the follow and sends your link right away."),
        ),
        sections=(
            Section(
                "Followers who actually care",
                paragraphs=(
                    "People who follow you to get something they asked for are interested in "
                    "what you share. That makes follow-to-unlock one of the most effective ways "
                    "to grow an engaged audience, not just a bigger number.",
                ),
            ),
            Section(
                "How the follow check works",
                paragraphs=(
                    "When someone taps the button, DamDM checks with Instagram whether they now "
                    "follow you. If not, it sends a friendly reminder and lets them try again, "
                    "up to three times, so nobody gets stuck.",
                ),
                bullets=(
                    "Write your own follow request and button text",
                    "Choose the message people see if they haven't followed yet",
                    "Or limit an automation to existing followers only",
                ),
            ),
            Section(
                "Great for",
                bullets=(
                    "Free guides, templates and presets",
                    "Giveaway entries and early-access lists",
                    "Recipe, workout or study notes",
                    "Launch links you want followers to see first",
                ),
            ),
        ),
        example=Example(
            trigger_label="Comment on your post",
            trigger="GUIDE",
            public_reply="Check your DMs 🎁",
            dm="Almost there! Follow the account and tap the button below and I'll send your link.",
            button="I'm following",
        ),
        faqs=(
            Faq(
                "What if someone taps the button without following?",
                "DamDM checks the follow with Instagram. If they haven't followed yet, they get a "
                "reminder and can try again, up to three times.",
            ),
            FAQ_SAFE,
            FAQ_PRICE,
        ),
        features=("comment-to-dm", "public-comment-replies", "story-reply-automation"),
    ),
    Page(
        slug="dm-keyword-auto-reply",
        icon="automations",
        name="DM keywords",
        title="Auto-reply to Instagram DMs by keyword",
        seo_title="Instagram DM Auto Reply by Keyword",
        description=(
            "Reply instantly when someone DMs you PRICE, MENU, LINK or any keyword. Answer common "
            "questions 24/7 on Instagram with DamDM, from ₹99/month."
        ),
        lead=(
            "Answer the questions you get every day, instantly. When a message contains your "
            "keyword, DamDM replies with the details and a link."
        ),
        summary="Answer PRICE, LINK or any keyword the moment it lands in your inbox.",
        steps=(
            ("Choose keywords", "PRICE, MENU, CATALOGUE, BOOK: whatever people ask about."),
            ("Write the reply", "Add the answer and a button to your website, store or WhatsApp."),
            ("Stay responsive", "Every matching DM gets an instant, consistent answer."),
        ),
        sections=(
            Section(
                "Never keep a customer waiting",
                paragraphs=(
                    "Most sales questions arrive at night, on weekends or while you're busy. A "
                    "fast reply is often the difference between a sale and a lost customer. DM "
                    "keyword replies answer immediately, and you can take over the chat any time.",
                ),
            ),
            Section(
                "Ideas to start with",
                bullets=(
                    "PRICE → your price list or catalogue",
                    "BOOK → your appointment or booking link",
                    "MENU → today's menu or order link",
                    "SIZE → your size chart",
                    "COURSE → course details and enrolment",
                ),
            ),
            Section(
                "Tips for better replies",
                bullets=(
                    "Keep the first reply short and add one clear button",
                    "Use words your customers actually type",
                    "Tell people how to reach a human if they need one",
                ),
            ),
        ),
        example=Example(
            trigger_label="Message in your inbox",
            trigger="Hi, what's the PRICE?",
            dm="Hi! 💛 Thanks for reaching out. Here are our prices:",
            button="See prices",
        ),
        faqs=(FAQ_MULTIPLE_KEYWORDS, FAQ_24_HOURS, FAQ_SAFE),
        features=("comment-to-dm", "story-reply-automation", "story-mention-automation"),
    ),
    Page(
        slug="story-reply-automation",
        icon="story",
        name="Story replies",
        title="Auto-reply when people answer your Instagram stories",
        seo_title="Instagram Story Reply Automation",
        description=(
            "Send an instant DM when someone replies to your Instagram story. Share links, thank "
            "fans and start conversations automatically with DamDM."
        ),
        lead=(
            "Stories are where your closest followers talk to you. DamDM replies to every story "
            "reply, or only those with a keyword, in seconds."
        ),
        summary="Turn story replies into conversations with an instant DM.",
        steps=(
            ("Post a story", "Ask viewers to reply with a word, or just to say hi."),
            ("Choose who gets a reply", "Everyone who replies, or only replies with a keyword."),
            ("DamDM answers", "Each reply gets your message and link straight away."),
        ),
        sections=(
            Section(
                "Links in stories, without link stickers",
                paragraphs=(
                    "Link stickers get few taps. Asking people to reply “YES” for the link starts "
                    "a real conversation, and the link arrives in a DM they can come back to.",
                ),
            ),
            Section(
                "Use it for",
                bullets=(
                    "Launches, drops and limited offers",
                    "Polls and questions with a follow-up link",
                    "Thanking people who react to your stories",
                    "Collecting interest before a live session",
                ),
            ),
        ),
        example=Example(
            trigger_label="Reply to your story",
            trigger="YES 🔥",
            dm="Thanks for replying to my story! 🙌 Here's the link:",
            button="Open link",
        ),
        faqs=(FAQ_24_HOURS, FAQ_SAFE, FAQ_PRICE),
        features=("story-mention-automation", "dm-keyword-auto-reply", "comment-to-dm"),
    ),
    Page(
        slug="story-mention-automation",
        icon="gift",
        name="Story mentions",
        title="Thank everyone who mentions you in their story",
        seo_title="Instagram Story Mention Auto DM",
        description=(
            "Automatically DM people who tag you in their Instagram stories. Thank customers, "
            "share a discount and encourage more mentions with DamDM."
        ),
        lead=(
            "When a customer tags you in their story, that's free promotion. DamDM thanks them "
            "instantly, with a discount code if you like."
        ),
        summary="Thank people who mention you in their stories, automatically.",
        steps=(
            ("Someone tags you", "A customer or fan mentions your account in their story."),
            ("DamDM notices", "The mention arrives through Instagram's official API."),
            ("They get a thank-you", "Your message, and an optional reward, lands in their DMs."),
        ),
        sections=(
            Section(
                "Reward the people who promote you",
                paragraphs=(
                    "A quick thank-you makes customers feel seen and makes them more likely to "
                    "tag you again. Adding a small reward, such as a discount on their next "
                    "order, turns mentions into repeat sales.",
                ),
                bullets=(
                    "Restaurants and cafés thanking diners",
                    "Stores rewarding customers who share their order",
                    "Creators thanking fans who share their content",
                    "Event hosts thanking guests",
                ),
            ),
        ),
        example=Example(
            trigger_label="Story mention",
            trigger="@yourbrand tagged you in their story",
            dm="Thank you so much for the mention! 💛 Here's 10% off your next order:",
            button="Claim offer",
        ),
        faqs=(FAQ_SAFE, FAQ_ACCOUNT_TYPE, FAQ_PRICE),
        features=("story-reply-automation", "dm-keyword-auto-reply", "comment-to-dm"),
    ),
    Page(
        slug="public-comment-replies",
        icon="reply",
        name="Public replies",
        title="Reply publicly to every Instagram comment",
        seo_title="Instagram Auto Reply to Comments Publicly",
        description=(
            "Automatically reply under Instagram comments with rotating messages. Show your "
            "audience you respond, and boost engagement on every post with DamDM."
        ),
        lead=(
            "A reply under each comment shows everyone your offer is real and keeps the "
            "conversation going. DamDM rotates your replies so they feel natural."
        ),
        summary="Rotating replies under comments show everyone your offer is real.",
        steps=(
            ("Write a few replies", "Add several short replies, like “Sent! Check your DMs 💌”."),
            ("Turn it on", "Use it with comment-to-DM or on its own."),
            ("DamDM rotates them", "Each comment gets one of your replies, so none look copied."),
        ),
        sections=(
            Section(
                "Why public replies matter",
                paragraphs=(
                    "When new visitors see that every comment gets an answer, they're more likely "
                    "to comment too. Replies also add to the conversation under your post, which "
                    "helps it reach more people.",
                ),
            ),
            Section(
                "Good public replies are",
                bullets=(
                    "Short and friendly",
                    "Clear about where to look: “Check your DMs”",
                    "Varied, so your comment section looks human",
                ),
            ),
        ),
        example=Example(
            trigger_label="Comment on your post",
            trigger="Want this! 😍",
            public_reply="Just sent you the details, check your DMs 💌",
        ),
        faqs=(FAQ_SAFE, FAQ_PRICE),
        features=("comment-to-dm", "follow-to-unlock", "dm-keyword-auto-reply"),
    ),
)
FEATURES_BY_SLUG = {page.slug: page for page in FEATURES}


# --- use cases ----------------------------------------------------------------

USE_CASES = (
    Page(
        slug="creators",
        icon="instagram",
        name="Creators and influencers",
        title="Instagram DM automation for creators",
        seo_title="Instagram DM Automation for Creators & Influencers",
        description=(
            "Share links, freebies and brand deals with every fan who comments. DamDM helps "
            "creators grow followers and clicks on autopilot for ₹99/month."
        ),
        lead=(
            "Your audience asks for links under every post. DamDM sends them in seconds, grows "
            "your followers, and gives you your evenings back."
        ),
        summary="Share links and freebies with every fan, and grow while you create.",
        steps=(
            ("Post your content", "Ask viewers to comment a keyword for the link."),
            ("Fans comment", "DamDM replies publicly and sends the link by DM."),
            ("You grow", "More comments, more reach, and new followers with Follow to unlock."),
        ),
        sections=(
            Section(
                "What creators automate",
                bullets=(
                    "Affiliate and product links from Reels",
                    "Free presets, templates and guides",
                    "Brand collaboration links and discount codes",
                    "YouTube, podcast or newsletter links",
                    "Replies to fans who mention you",
                ),
            ),
            Section(
                "Why it helps you grow",
                paragraphs=(
                    "“Comment LINK” posts get far more comments than “link in bio”. More "
                    "comments signal to Instagram that people care about your post, so it can "
                    "reach more people. With Follow to unlock, each of those comments can also "
                    "become a follower.",
                ),
            ),
        ),
        faqs=(FAQ_SAFE, FAQ_ACCOUNT_TYPE, FAQ_PRICE),
        features=("comment-to-dm", "follow-to-unlock", "story-mention-automation"),
    ),
    Page(
        slug="coaches",
        icon="document",
        name="Coaches and course sellers",
        title="Instagram DM automation for coaches and course sellers",
        seo_title="Instagram Automation for Coaches & Course Sellers",
        description=(
            "Collect leads from Instagram comments and DMs. Send free lessons, webinar links and "
            "course details instantly with DamDM, from ₹99/month."
        ),
        lead=(
            "Turn content into leads. Offer a free lesson or checklist, and DamDM delivers it "
            "the moment someone asks, while you teach."
        ),
        summary="Deliver free lessons and webinar links, and turn followers into students.",
        steps=(
            ("Offer something useful", "A free class, checklist or mini-course."),
            ("People ask for it", "They comment or DM your keyword."),
            ("DamDM delivers", "They get the link instantly, and you get a warm lead."),
        ),
        sections=(
            Section(
                "Automations coaches use",
                bullets=(
                    "Comment WEBINAR → registration link",
                    "Comment NOTES → free PDF or checklist",
                    "DM COURSE → course details and enrolment link",
                    "DM BOOK → discovery-call booking link",
                    "Story replies → early-bird offers",
                ),
            ),
            Section(
                "Fast answers sell courses",
                paragraphs=(
                    "Students often decide in the moment. An instant, helpful reply with a clear "
                    "next step keeps that interest from fading, and you can still step in "
                    "personally when a conversation needs you.",
                ),
            ),
        ),
        faqs=(FAQ_24_HOURS, FAQ_SAFE, FAQ_PRICE),
        features=("comment-to-dm", "dm-keyword-auto-reply", "story-reply-automation"),
    ),
    Page(
        slug="online-stores",
        icon="card",
        name="Online stores and D2C brands",
        title="Instagram DM automation for online stores",
        seo_title="Instagram DM Automation for Online Stores & D2C",
        description=(
            "Answer PRICE and SIZE questions instantly, send product links from Reels and reward "
            "customers who tag you. DamDM for Instagram shops, ₹99/month."
        ),
        lead=(
            "Every “price?” comment is a buyer. DamDM answers it in seconds with the product "
            "link, so you sell even when you're packing orders."
        ),
        summary="Answer price questions and send product links, day and night.",
        steps=(
            ("Post a product", "Ask shoppers to comment PRICE or SHOP."),
            ("They get the link", "DamDM sends the price and a buy button by DM."),
            ("You sell more", "Fewer lost buyers, and customers who come back."),
        ),
        sections=(
            Section(
                "What stores automate",
                bullets=(
                    "Comment PRICE → product page with price",
                    "DM SIZE → size chart",
                    "DM TRACK → order-tracking page",
                    "DM COD → payment and delivery options",
                    "Story mention → thank-you with a discount code",
                ),
            ),
            Section(
                "Stop losing “price please” customers",
                paragraphs=(
                    "Shoppers who ask for a price and don't hear back quickly move on. Automated "
                    "replies give every one of them an answer and a link, and your team can "
                    "focus on the conversations that need a person.",
                ),
            ),
        ),
        faqs=(FAQ_MULTIPLE_KEYWORDS, FAQ_SAFE, FAQ_PRICE),
        features=("comment-to-dm", "dm-keyword-auto-reply", "story-mention-automation"),
    ),
    Page(
        slug="local-businesses",
        icon="home",
        name="Local businesses",
        title="Instagram DM automation for local businesses",
        seo_title="Instagram Automation for Salons, Cafés & Local Shops",
        description=(
            "Salons, cafés, gyms, clinics and real-estate agents: reply to Instagram enquiries "
            "instantly and share booking links with DamDM for ₹99/month."
        ),
        lead=(
            "Salons, cafés, gyms, clinics and property agents get the same questions every day. "
            "DamDM answers them instantly with your menu, prices or booking link."
        ),
        summary="Share menus, prices and booking links the moment people ask.",
        steps=(
            ("List common questions", "Menu, prices, timings, location, booking."),
            ("Set keywords", "MENU, BOOK, PRICE, LOCATION, and your replies."),
            ("Answer everyone", "Every enquiry gets a fast reply, even after hours."),
        ),
        sections=(
            Section(
                "Ideas by business",
                bullets=(
                    "Salons and spas: DM BOOK → appointment link",
                    "Cafés and restaurants: DM MENU → menu and order link",
                    "Gyms and studios: comment TRIAL → free-trial booking",
                    "Clinics: DM TIMINGS → hours and location",
                    "Real estate: comment DETAILS → brochure and site-visit link",
                ),
            ),
            Section(
                "More bookings, less typing",
                paragraphs=(
                    "Answering the same questions by hand takes hours each week. With DamDM, "
                    "customers get what they need immediately, and you only step in for the "
                    "conversations that need a personal touch.",
                ),
            ),
        ),
        faqs=(FAQ_24_HOURS, FAQ_SAFE, FAQ_PRICE),
        features=("dm-keyword-auto-reply", "story-mention-automation", "comment-to-dm"),
    ),
)
USE_CASES_BY_SLUG = {page.slug: page for page in USE_CASES}


# --- general FAQ --------------------------------------------------------------

FAQ_GROUPS = (
    ("Getting started", (
        Faq(
            "What is DamDM?",
            "DamDM is an Instagram automation tool. It replies to comments, DMs, story replies "
            "and story mentions for you, and sends links by DM, using Meta's official API.",
        ),
        FAQ_ACCOUNT_TYPE,
        FAQ_FACEBOOK,
        FAQ_PASSWORD,
        Faq(
            "How long does setup take?",
            "About a minute. Connect Instagram, pick a template such as Comment to DM, edit the "
            "words and turn it on.",
        ),
    )),
    ("Features", (
        Faq(
            "What can DamDM automate?",
            "Comment-to-DM, public comment replies, follow-to-unlock, DM keyword replies, story "
            "reply replies and thank-you messages for story mentions.",
        ),
        FAQ_MULTIPLE_KEYWORDS,
        Faq(
            "Can I choose which posts an automation runs on?",
            "Yes: all posts, specific posts or Reels, or your next post automatically.",
        ),
        FAQ_PRIVATE_REPLY_LIMIT,
        FAQ_24_HOURS,
    )),
    ("Safety", (
        FAQ_SAFE,
        Faq(
            "Can DamDM send messages to people who haven't contacted me?",
            "No. Instagram doesn't allow that, and DamDM doesn't try. Every automated message is "
            "a response to something the person did.",
        ),
    )),
    ("Pricing and billing", (
        FAQ_PRICE,
        FAQ_TRIAL,
        Faq(
            "How can I pay?",
            "With UPI AutoPay, cards or netbanking, through Razorpay. Your bank reminds you "
            "before each monthly charge.",
        ),
        FAQ_CANCEL,
        FAQ_ALTERNATIVE,
    )),
)
ALL_FAQS = tuple(faq for _, faqs in FAQ_GROUPS for faq in faqs)
PRICING_FAQS = (FAQ_PRICE, FAQ_TRIAL, FAQ_CANCEL, FAQ_ALTERNATIVE)
HOME_FAQS = (FAQ_SAFE, FAQ_ACCOUNT_TYPE, FAQ_TRIAL, FAQ_ALTERNATIVE)
ALTERNATIVE_FAQS = (FAQ_ALTERNATIVE, FAQ_SAFE, FAQ_TRIAL, FAQ_CANCEL)


# --- guides -------------------------------------------------------------------

GUIDES = (
    Guide(
        slug="auto-reply-instagram-comments-with-dm",
        title="How to auto-reply to Instagram comments with a DM",
        seo_title="How to Auto Reply to Instagram Comments With a DM",
        description=(
            "Step-by-step guide to sending an automatic DM when someone comments on your "
            "Instagram post or Reel, with keyword ideas and tips that get more comments."
        ),
        summary="Set up comment-to-DM in minutes, with keyword ideas and caption tips.",
        published=date(2026, 9, 17),
        updated=date(2026, 9, 17),
        read_minutes=6,
        faqs=(FAQ_PRIVATE_REPLY_LIMIT, FAQ_ACCOUNT_TYPE, FAQ_SAFE),
    ),
    Guide(
        slug="grow-instagram-followers-with-dm-automation",
        title="How to grow Instagram followers with DM automation",
        seo_title="Grow Instagram Followers With DM Automation",
        description=(
            "Use comment-to-DM and follow-to-unlock to turn Reels into followers. A practical "
            "playbook with post ideas, captions and mistakes to avoid."
        ),
        summary="A practical playbook for turning comments into engaged followers.",
        published=date(2026, 9, 17),
        updated=date(2026, 9, 17),
        read_minutes=7,
        faqs=(FAQ_SAFE, FAQ_24_HOURS),
    ),
    Guide(
        slug="instagram-dm-automation-for-small-businesses-india",
        title="Instagram DM automation for small businesses in India",
        seo_title="Instagram DM Automation for Small Businesses in India",
        description=(
            "How Indian shops, salons, cafés and coaches use Instagram DM automation to answer "
            "enquiries instantly, with ready-to-use keyword replies."
        ),
        summary="Ready-to-use keyword replies for shops, salons, cafés and coaches.",
        published=date(2026, 9, 17),
        updated=date(2026, 9, 17),
        read_minutes=7,
        faqs=(FAQ_PRICE, FAQ_24_HOURS, FAQ_SAFE),
    ),
    Guide(
        slug="switch-to-instagram-professional-account",
        title="How to switch to an Instagram Business or Creator account",
        seo_title="Switch to an Instagram Business or Creator Account",
        description=(
            "Instagram automation needs a professional account. Learn the difference between "
            "Business and Creator accounts and how to switch in under a minute."
        ),
        summary="Which professional account to choose, and how to switch in a minute.",
        published=date(2026, 9, 17),
        updated=date(2026, 9, 17),
        read_minutes=4,
        faqs=(FAQ_ACCOUNT_TYPE, FAQ_FACEBOOK, FAQ_PASSWORD),
    ),
)
GUIDES_BY_SLUG = {guide.slug: guide for guide in GUIDES}
