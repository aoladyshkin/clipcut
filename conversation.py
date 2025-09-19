from telegram.ext import ConversationHandler, CommandHandler, MessageHandler, filters, CallbackQueryHandler
from commands import start, topup_start
from handlers import (
    get_url,
    get_ai_transcription,
    get_shorts_number_auto,
    get_shorts_number_manual,
    get_layout,
    get_bottom_video,
    get_subtitles_type,
    get_subtitle_style,
    get_capitalize,
    confirm_config,
    cancel_conversation,
    topup_stars,
    topup_crypto,
    cancel_topup,
    send_invoice_for_stars,
    back_to_get_ai_transcription,
    back_to_shorts_number,
    back_to_layout,
    back_to_bottom_video,
    back_to_subtitles_type,
    back_to_subtitle_style,
    back_to_get_capitalize,
    back_to_topup_method,
)
from states import (
    GET_URL,
    GET_SUBTITLE_STYLE,
    GET_BOTTOM_VIDEO,
    GET_LAYOUT,
    GET_SUBTITLES_TYPE,
    GET_CAPITALIZE,
    CONFIRM_CONFIG,
    GET_AI_TRANSCRIPTION,
    GET_SHORTS_NUMBER,
    GET_TOPUP_METHOD,
    GET_TOPUP_PACKAGE
)

def get_conv_handler():
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start), CommandHandler("topup", topup_start)],
        states={
            GET_URL: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_url)],
            GET_AI_TRANSCRIPTION: [
                CallbackQueryHandler(get_ai_transcription, pattern='^(youtube|ai)$'),
            ],
            GET_SHORTS_NUMBER: [
                CallbackQueryHandler(get_shorts_number_auto, pattern='^auto$'),
                MessageHandler(filters.TEXT & ~filters.COMMAND, get_shorts_number_manual),
                CallbackQueryHandler(back_to_get_ai_transcription, pattern='^back_to_get_ai_transcription$')
            ],
            GET_LAYOUT: [
                CallbackQueryHandler(get_layout, pattern='^(top_bottom|main_only)$'),
                CallbackQueryHandler(back_to_shorts_number, pattern='^back_to_shorts_number$')
            ],
            GET_BOTTOM_VIDEO: [
                CallbackQueryHandler(get_bottom_video, pattern='^(gta|minecraft|none)$'),
                CallbackQueryHandler(back_to_layout, pattern='^back_to_layout$')
            ],
            GET_SUBTITLES_TYPE: [
                CallbackQueryHandler(get_subtitles_type, pattern='^(word-by-word|phrases)$'),
                CallbackQueryHandler(back_to_bottom_video, pattern='^back_to_bottom_video$'),
                CallbackQueryHandler(back_to_layout, pattern='^back_to_layout$')
            ],
            GET_SUBTITLE_STYLE: [
                CallbackQueryHandler(get_subtitle_style, pattern='^(white|yellow)$'),
                CallbackQueryHandler(back_to_subtitles_type, pattern='^back_to_subtitles_type$')
            ],
            GET_CAPITALIZE: [
                CallbackQueryHandler(get_capitalize, pattern='^(true|false)$'),
                CallbackQueryHandler(back_to_subtitle_style, pattern='^back_to_subtitle_style$')
            ],
            CONFIRM_CONFIG: [
                CallbackQueryHandler(confirm_config, pattern='^confirm$'),
                CallbackQueryHandler(cancel_conversation, pattern='^cancel$'),
                CallbackQueryHandler(back_to_get_capitalize, pattern='^back_to_get_capitalize$')
            ],
            GET_TOPUP_METHOD: [
                CallbackQueryHandler(topup_stars, pattern='^topup_stars$'),
                CallbackQueryHandler(topup_crypto, pattern='^topup_crypto$'),
                CallbackQueryHandler(cancel_topup, pattern='^cancel_topup$')
            ],
            GET_TOPUP_PACKAGE: [
                CallbackQueryHandler(send_invoice_for_stars, pattern='^topup_\d+_\d+$'),
                CallbackQueryHandler(back_to_topup_method, pattern='^back_to_topup_method$'),
				CallbackQueryHandler(cancel_topup, pattern='^cancel_topup$')
            ]
        },
        fallbacks=[CommandHandler("start", start)],
        conversation_timeout=600, # 10 минут на диалог
        allow_reentry=True
    )
    return conv_handler